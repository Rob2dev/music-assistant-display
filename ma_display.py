#!/usr/bin/env python3
"""Now-playing screen for a Music Assistant player (sendspin-cli) on a Raspberry Pi.

Draws straight onto a 480x320 SPI display (/dev/fb0, RGB565) and reads the XPT2046
touch panel through evdev. No token needed for the player screen:

- Controls and basic state come from the control socket of sendspin-cli (the audio player).
- Title/artist/album/progress/cover come from our own Sendspin client with the metadata,
  artwork, controller and visualizer roles. Music Assistant sees it as a DISPLAY player.
  If the player is playing but the display sits in a different group, the display sends
  'switch' until the titles match.
- Picking radio stations needs a Music Assistant API token (see README).

Usage:
  ma_display.py                 normal mode (systemd service)
  ma_display.py --demo          show a demo state on the display
  ma_display.py --demo --png X  write a demo frame to a PNG (no display needed)
  ma_display.py --calibrate     calibrate the touch panel (stop the service first, as root)
"""
import argparse
import asyncio
import contextlib
import copy
import datetime
import glob
import io
import json
import logging
import math
import os
import queue
import socket
import subprocess
import threading
import time

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageStat

log = logging.getLogger("ma-display")

CONFIG_PATH = os.environ.get("MA_DISPLAY_CONFIG", "/etc/ma-display/config.json")
W, H = 480, 320
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

HOSTNAME = socket.gethostname()

DEFAULTS = {
    "control_socket": "/run/sendspin-cli/control.sock",
    # How Music Assistant lists this display. sendspin-cli names the player after the hostname
    # by default; override "ma_player_name" if you gave the player another name.
    "sendspin_name": HOSTNAME + " display",
    "sendspin_port": 8929,
    "state_dir": "/var/lib/ma-display",
    # Music Assistant API, only needed to pick stations: set the URL (e.g. "http://192.168.1.10:8095")
    # and the token (ma-display-set-token). Without both the station picker is disabled.
    "ma_url": "",
    "ma_token": "",
    "ma_player_name": HOSTNAME,
    # Framebuffer of the SPI panel. "auto" looks it up by the driver name in "fb_name": the number
    # depends on the order the drivers load in (the HDMI firmware framebuffer can still be fb0).
    # Set a path such as "/dev/fb1" to pin it.
    "fb": "auto",
    "fb_name": "ili9486drmfb",
    "touch_name": "ADS7846 Touchscreen",
    "volume_step": 5,
    # What the power menu runs (long-press the clock). The service user needs permission to run
    # these: see polkit/50-ma-display-power.rules.
    "shutdown_command": ["systemctl", "poweroff"],
    "restart_command": ["systemctl", "reboot"],
    # Minutes without playback before the clock screen appears.
    "clock_after_min": 5,
    # Idle screen: "analog" (clock face, date and a tile for the last station, drifting slowly) or
    # "digital" (the plain big clock). After "night_after_min" minutes without playback it turns into a
    # dim digital time (0 = never). "idle_fps" is how often the hands are redrawn.
    "idle_style": "analog",
    "night_after_min": 60,
    "idle_fps": 4,
    # Cover art (also the blurred background) and the spectrum analyser on the player screen.
    "show_cover": True,
    "show_analyser": True,
    # UI language: en, nl, fr or de. Empty = derive from $LANG, else English.
    "language": "",
    # Raw touch values -> screen coordinates (after the optional swap). Measured with --calibrate.
    "calibration": {"swap_xy": True, "x_min": 3936, "x_max": 227, "y_min": 268, "y_max": 3880},
}

# Colours
BG = (14, 14, 18)
FG = (240, 240, 244)
FG_DIM = (170, 170, 180)
FG_FAINT = (110, 110, 120)
FG_SOFT = (150, 150, 160)  # secondary text on a (sometimes light) background: time, album line
BG_MAX_LUMA = 55  # maximum mean brightness of the blurred cover background
ACCENT = (255, 138, 61)  # only used by the calibration screen now
ACCENT_FALLBACK = (79, 195, 247)  # ice blue, for covers without a usable colour
ERROR = (235, 87, 87)

# ---------------------------------------------------------------- translations
# Every UI string lives here. English is the fallback for a missing key or language, so a
# half-finished translation never leaves a blank on screen. Keep strings short: the layout
# is sized for the shortest language, long ones are truncated with an ellipsis.

STRINGS = {
    "en": {
        "status_playing": "NOW PLAYING", "status_paused": "PAUSED", "status_idle": "IDLE",
        "status_stopped": "STOPPED", "status_off": "OFF", "status_unavailable": "UNAVAILABLE",
        "live": "LIVE",
        "queue_empty": "Nothing in the queue",
        "player_unreachable": "Player unreachable",
        "resume_hint": "Last played · press play to resume",
        "tab_fav": "Favourites", "tab_all": "All",
        "stations_loading": "Loading stations…", "stations_none": "No stations",
        "stations_failed": "Could not load stations", "tap_retry": "Tap to retry",
        "volume": "VOLUME", "muted": "muted",
        "failed": "Failed", "play_failed": "Playback failed", "resume_failed": "Could not resume",
        "retrying": "Retrying…",
        "err_no_ma": "No connection to Music Assistant",
        "err_no_player": "sendspin-cli unreachable",
        "err_pairing": "Pairing code for MA: {pin}",
        "clock_last": "Last: ", "clock_hint": "tap to control",
        "calibrate_title": "Calibrate touch", "calibrate_step": "Tap the crosshair precisely ({n}/3).",
        "calibrate_done": "Calibration done",
        "menu_title": "MENU", "menu_settings": "Settings",
        "power_shutdown": "Shut down", "power_restart": "Restart",
        "power_cancel": "Cancel", "power_busy_shutdown": "Shutting down…", "power_busy_restart": "Restarting…",
        "power_wait_shutdown": "Wait a moment before unplugging the power.",
        "power_wait_restart": "The display is back in about a minute.",
        "power_failed": "Power command failed",
        "status_starting": "STARTING", "starting": "Starting…",
        "tap_resume": "Tap to resume", "date_short": "{month} {day}",
        "tab_rooms": "Rooms", "rooms_this": "This player", "rooms_offline": "offline",
        "rooms_none": "No other rooms", "rooms_loading": "Loading rooms…", "rooms_failed": "Could not load rooms",
        "room_add_q": "Add {name} to the group?", "room_remove_q": "Remove {name} from the group?",
        "room_add": "Add", "room_remove": "Remove", "room_failed": "Could not change the group",
        "room_done_add": "{name} added", "room_done_remove": "{name} removed",
        "tab_general": "General", "tab_display": "Display", "tab_info": "Info", "set_language": "Language",
        "set_cover": "Cover art", "set_analyser": "Spectrum analyser",
        "set_idle_style": "Idle screen", "set_idle_after": "Idle after", "set_night_after": "Night mode after",
        "set_volume_step": "Volume step", "set_reset": "Reset to defaults",
        "style_analog": "Analog", "style_digital": "Digital", "off": "Off",
        "info_host": "Hostname", "info_ip": "IP address", "info_uptime": "Uptime", "info_ma": "Music Assistant",
        "info_temp": "Temperature", "ma_connected": "connected", "ma_disconnected": "not connected",
        "settings_reset_done": "Defaults restored", "settings_failed": "Could not save",
        "date": "{weekday}, {month} {day}",
        "weekdays": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        "months": ["January", "February", "March", "April", "May", "June", "July", "August",
                   "September", "October", "November", "December"],
    },
    "nl": {
        "status_playing": "SPEELT NU", "status_paused": "GEPAUZEERD", "status_idle": "NIET ACTIEF",
        "status_stopped": "GESTOPT", "status_off": "UIT", "status_unavailable": "NIET BEREIKBAAR",
        "live": "LIVE",
        "queue_empty": "Niets in de wachtrij",
        "player_unreachable": "Player niet bereikbaar",
        "resume_hint": "Laatst gespeeld · druk op play om te hervatten",
        "tab_fav": "Favorieten", "tab_all": "Alle",
        "stations_loading": "Zenders laden…", "stations_none": "Geen zenders",
        "stations_failed": "Zenders laden mislukt", "tap_retry": "Tik om opnieuw te proberen",
        "volume": "VOLUME", "muted": "gedempt",
        "failed": "Mislukt", "play_failed": "Afspelen mislukt", "resume_failed": "Hervatten mislukt",
        "retrying": "Opnieuw proberen…",
        "err_no_ma": "Geen verbinding met Music Assistant",
        "err_no_player": "sendspin-cli onbereikbaar",
        "err_pairing": "Koppelcode voor MA: {pin}",
        "clock_last": "Laatst: ", "clock_hint": "tik om te bedienen",
        "calibrate_title": "Touch kalibreren", "calibrate_step": "Tik precies op het kruisje ({n}/3).",
        "calibrate_done": "Kalibratie klaar",
        "menu_title": "MENU", "menu_settings": "Instellingen",
        "power_shutdown": "Uitzetten", "power_restart": "Herstarten",
        "power_cancel": "Annuleren", "power_busy_shutdown": "Wordt uitgezet…", "power_busy_restart": "Wordt herstart…",
        "power_wait_shutdown": "Wacht even voordat je de stroom eraf haalt.",
        "power_wait_restart": "Het scherm is over ongeveer een minuut terug.",
        "power_failed": "Stroomopdracht mislukt",
        "status_starting": "STARTEN", "starting": "Starten…",
        "tap_resume": "Tik om te hervatten", "date_short": "{day} {month}",
        "tab_rooms": "Kamers", "rooms_this": "Deze speler", "rooms_offline": "offline",
        "rooms_none": "Geen andere kamers", "rooms_loading": "Kamers laden…", "rooms_failed": "Kamers laden mislukt",
        "room_add_q": "{name} aan de groep toevoegen?", "room_remove_q": "{name} uit de groep halen?",
        "room_add": "Toevoegen", "room_remove": "Verwijderen", "room_failed": "Groep wijzigen mislukt",
        "room_done_add": "{name} toegevoegd", "room_done_remove": "{name} verwijderd",
        "tab_general": "Algemeen", "tab_display": "Scherm", "tab_info": "Info", "set_language": "Taal",
        "set_cover": "Albumhoes", "set_analyser": "Spectrumanalyser",
        "set_idle_style": "Idle-scherm", "set_idle_after": "Idle na", "set_night_after": "Nachtstand na",
        "set_volume_step": "Volumestap", "set_reset": "Standaardwaarden herstellen",
        "style_analog": "Analoog", "style_digital": "Digitaal", "off": "Uit",
        "info_host": "Hostnaam", "info_ip": "IP-adres", "info_uptime": "Uptime", "info_ma": "Music Assistant",
        "info_temp": "Temperatuur", "ma_connected": "verbonden", "ma_disconnected": "niet verbonden",
        "settings_reset_done": "Standaardwaarden hersteld", "settings_failed": "Opslaan mislukt",
        "date": "{weekday} {day} {month}",
        "weekdays": ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"],
        "months": ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
                   "september", "oktober", "november", "december"],
    },
    "fr": {
        "status_playing": "EN LECTURE", "status_paused": "EN PAUSE", "status_idle": "INACTIF",
        "status_stopped": "ARRÊTÉ", "status_off": "ÉTEINT", "status_unavailable": "INDISPONIBLE",
        "live": "DIRECT",
        "queue_empty": "File d'attente vide",
        "player_unreachable": "Lecteur injoignable",
        "resume_hint": "Dernière lecture · appuyez sur lecture pour reprendre",
        "tab_fav": "Favoris", "tab_all": "Toutes",
        "stations_loading": "Chargement des stations…", "stations_none": "Aucune station",
        "stations_failed": "Échec du chargement des stations", "tap_retry": "Appuyez pour réessayer",
        "volume": "VOLUME", "muted": "muet",
        "failed": "Échec", "play_failed": "Échec de la lecture", "resume_failed": "Échec de la reprise",
        "retrying": "Nouvelle tentative…",
        "err_no_ma": "Pas de connexion à Music Assistant",
        "err_no_player": "sendspin-cli injoignable",
        "err_pairing": "Code d'appairage MA : {pin}",
        "clock_last": "Dernier : ", "clock_hint": "touchez pour contrôler",
        "calibrate_title": "Calibrer l'écran tactile", "calibrate_step": "Touchez précisément la croix ({n}/3).",
        "calibrate_done": "Calibrage terminé",
        "menu_title": "MENU", "menu_settings": "Réglages",
        "power_shutdown": "Éteindre", "power_restart": "Redémarrer",
        "power_cancel": "Annuler", "power_busy_shutdown": "Arrêt en cours…", "power_busy_restart": "Redémarrage…",
        "power_wait_shutdown": "Patientez avant de débrancher l'alimentation.",
        "power_wait_restart": "L'écran revient dans environ une minute.",
        "power_failed": "Échec de la commande",
        "status_starting": "DÉMARRAGE", "starting": "Démarrage…",
        "tap_resume": "Touchez pour reprendre", "date_short": "{day} {month}",
        "tab_rooms": "Pièces", "rooms_this": "Ce lecteur", "rooms_offline": "hors ligne",
        "rooms_none": "Aucune autre pièce", "rooms_loading": "Chargement des pièces…", "rooms_failed": "Échec du chargement des pièces",
        "room_add_q": "Ajouter {name} au groupe ?", "room_remove_q": "Retirer {name} du groupe ?",
        "room_add": "Ajouter", "room_remove": "Retirer", "room_failed": "Échec de la modification du groupe",
        "room_done_add": "{name} ajouté", "room_done_remove": "{name} retiré",
        "tab_general": "Général", "tab_display": "Écran", "tab_info": "Infos", "set_language": "Langue",
        "set_cover": "Pochette", "set_analyser": "Analyseur de spectre",
        "set_idle_style": "Écran de veille", "set_idle_after": "Veille après", "set_night_after": "Mode nuit après",
        "set_volume_step": "Pas du volume", "set_reset": "Rétablir les valeurs par défaut",
        "style_analog": "Analogique", "style_digital": "Numérique", "off": "Désactivé",
        "info_host": "Nom d'hôte", "info_ip": "Adresse IP", "info_uptime": "Disponibilité", "info_ma": "Music Assistant",
        "info_temp": "Température", "ma_connected": "connecté", "ma_disconnected": "non connecté",
        "settings_reset_done": "Valeurs par défaut rétablies", "settings_failed": "Échec de l'enregistrement",
        "date": "{weekday} {day} {month}",
        "weekdays": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
        "months": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
                   "septembre", "octobre", "novembre", "décembre"],
    },
    "de": {
        "status_playing": "WIEDERGABE", "status_paused": "PAUSIERT", "status_idle": "INAKTIV",
        "status_stopped": "GESTOPPT", "status_off": "AUS", "status_unavailable": "NICHT ERREICHBAR",
        "live": "LIVE",
        "queue_empty": "Warteschlange leer",
        "player_unreachable": "Player nicht erreichbar",
        "resume_hint": "Zuletzt gespielt · Play drücken zum Fortsetzen",
        "tab_fav": "Favoriten", "tab_all": "Alle",
        "stations_loading": "Sender werden geladen…", "stations_none": "Keine Sender",
        "stations_failed": "Sender konnten nicht geladen werden", "tap_retry": "Zum Wiederholen tippen",
        "volume": "LAUTSTÄRKE", "muted": "stumm",
        "failed": "Fehlgeschlagen", "play_failed": "Wiedergabe fehlgeschlagen",
        "resume_failed": "Fortsetzen fehlgeschlagen",
        "retrying": "Neuer Versuch…",
        "err_no_ma": "Keine Verbindung zu Music Assistant",
        "err_no_player": "sendspin-cli nicht erreichbar",
        "err_pairing": "Kopplungscode für MA: {pin}",
        "clock_last": "Zuletzt: ", "clock_hint": "zum Bedienen tippen",
        "calibrate_title": "Touch kalibrieren", "calibrate_step": "Tippe genau auf das Kreuz ({n}/3).",
        "calibrate_done": "Kalibrierung abgeschlossen",
        "menu_title": "MENÜ", "menu_settings": "Einstellungen",
        "power_shutdown": "Herunterfahren", "power_restart": "Neu starten",
        "power_cancel": "Abbrechen", "power_busy_shutdown": "Wird heruntergefahren…", "power_busy_restart": "Wird neu gestartet…",
        "power_wait_shutdown": "Bitte kurz warten, bevor du den Strom trennst.",
        "power_wait_restart": "Das Display ist in etwa einer Minute wieder da.",
        "power_failed": "Befehl fehlgeschlagen",
        "status_starting": "STARTET", "starting": "Startet…",
        "tap_resume": "Tippen zum Fortsetzen", "date_short": "{day}. {month}",
        "tab_rooms": "Räume", "rooms_this": "Dieser Player", "rooms_offline": "offline",
        "rooms_none": "Keine weiteren Räume", "rooms_loading": "Räume werden geladen…", "rooms_failed": "Räume konnten nicht geladen werden",
        "room_add_q": "{name} zur Gruppe hinzufügen?", "room_remove_q": "{name} aus der Gruppe entfernen?",
        "room_add": "Hinzufügen", "room_remove": "Entfernen", "room_failed": "Gruppe konnte nicht geändert werden",
        "room_done_add": "{name} hinzugefügt", "room_done_remove": "{name} entfernt",
        "tab_general": "Allgemein", "tab_display": "Anzeige", "tab_info": "Info", "set_language": "Sprache",
        "set_cover": "Cover", "set_analyser": "Spektrumanalyzer",
        "set_idle_style": "Ruhebildschirm", "set_idle_after": "Ruhe nach", "set_night_after": "Nachtmodus nach",
        "set_volume_step": "Lautstärkestufe", "set_reset": "Auf Standard zurücksetzen",
        "style_analog": "Analog", "style_digital": "Digital", "off": "Aus",
        "info_host": "Hostname", "info_ip": "IP-Adresse", "info_uptime": "Laufzeit", "info_ma": "Music Assistant",
        "info_temp": "Temperatur", "ma_connected": "verbunden", "ma_disconnected": "nicht verbunden",
        "settings_reset_done": "Standardwerte wiederhergestellt", "settings_failed": "Speichern fehlgeschlagen",
        "date": "{weekday}, {day}. {month}",
        "weekdays": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"],
        "months": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
                   "September", "Oktober", "November", "Dezember"],
    },
    "es": {
        "status_playing": "REPRODUCIENDO",
        "status_paused": "EN PAUSA",
        "status_idle": "INACTIVO",
        "status_stopped": "DETENIDO",
        "status_off": "APAGADO",
        "status_unavailable": "NO DISPONIBLE",
        "live": "EN VIVO",
        "queue_empty": "Nada en la cola",
        "player_unreachable": "Reproductor no disponible",
        "resume_hint": "Última reproducción · pulsa play para reanudar",
        "tab_fav": "Favoritas",
        "tab_all": "Todas",
        "stations_loading": "Cargando emisoras…",
        "stations_none": "Sin emisoras",
        "stations_failed": "No se pudieron cargar las emisoras",
        "tap_retry": "Toca para reintentar",
        "volume": "VOLUMEN",
        "muted": "silenciado",
        "failed": "Error",
        "play_failed": "Error de reproducción",
        "resume_failed": "No se pudo reanudar",
        "retrying": "Reintentando…",
        "err_no_ma": "Sin conexión con Music Assistant",
        "err_no_player": "sendspin-cli no disponible",
        "err_pairing": "Código de emparejamiento de MA: {pin}",
        "clock_last": "Último: ",
        "clock_hint": "toca para controlar",
        "calibrate_title": "Calibrar pantalla táctil",
        "calibrate_step": "Toca la cruz con precisión ({n}/3).",
        "calibrate_done": "Calibración completada",
        "menu_title": "MENÚ",
        "menu_settings": "Ajustes",
        "power_shutdown": "Apagar",
        "power_restart": "Reiniciar",
        "power_cancel": "Cancelar",
        "power_busy_shutdown": "Apagando…",
        "power_busy_restart": "Reiniciando…",
        "power_wait_shutdown": "Espera un momento antes de desenchufar.",
        "power_wait_restart": "La pantalla vuelve en un minuto aproximadamente.",
        "power_failed": "Error en la orden de energía",
        "status_starting": "INICIANDO",
        "starting": "Iniciando…",
        "tap_resume": "Toca para reanudar",
        "date_short": "{day} de {month}",
        "tab_rooms": "Salas",
        "rooms_this": "Este reproductor",
        "rooms_offline": "sin conexión",
        "rooms_none": "No hay más salas",
        "rooms_loading": "Cargando salas…",
        "rooms_failed": "No se pudieron cargar las salas",
        "room_add_q": "¿Añadir {name} al grupo?",
        "room_remove_q": "¿Quitar {name} del grupo?",
        "room_add": "Añadir",
        "room_remove": "Quitar",
        "room_failed": "No se pudo cambiar el grupo",
        "room_done_add": "{name} añadido",
        "room_done_remove": "{name} quitado",
        "tab_general": "General",
        "tab_display": "Pantalla",
        "tab_info": "Info",
        "set_language": "Idioma",
        "set_cover": "Carátula",
        "set_analyser": "Analizador de espectro",
        "set_idle_style": "Pantalla en reposo",
        "set_idle_after": "Reposo tras",
        "set_night_after": "Modo noche tras",
        "set_volume_step": "Paso de volumen",
        "set_reset": "Restablecer valores",
        "style_analog": "Analógico",
        "style_digital": "Digital",
        "off": "Apagado",
        "info_host": "Nombre de host",
        "info_ip": "Dirección IP",
        "info_uptime": "Tiempo activo",
        "info_ma": "Music Assistant",
        "info_temp": "Temperatura",
        "ma_connected": "conectado",
        "ma_disconnected": "sin conexión",
        "settings_reset_done": "Valores restablecidos",
        "settings_failed": "No se pudo guardar",
        "date": "{weekday} {day} de {month}",
        "weekdays": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
        "months": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
    },
    "it": {
        "status_playing": "IN RIPRODUZIONE",
        "status_paused": "IN PAUSA",
        "status_idle": "INATTIVO",
        "status_stopped": "FERMATO",
        "status_off": "SPENTO",
        "status_unavailable": "NON DISPONIBILE",
        "live": "LIVE",
        "queue_empty": "Niente in coda",
        "player_unreachable": "Lettore non raggiungibile",
        "resume_hint": "Ultimo ascolto · premi play per riprendere",
        "tab_fav": "Preferiti",
        "tab_all": "Tutte",
        "stations_loading": "Caricamento stazioni…",
        "stations_none": "Nessuna stazione",
        "stations_failed": "Impossibile caricare le stazioni",
        "tap_retry": "Tocca per riprovare",
        "volume": "VOLUME",
        "muted": "muto",
        "failed": "Errore",
        "play_failed": "Riproduzione non riuscita",
        "resume_failed": "Impossibile riprendere",
        "retrying": "Nuovo tentativo…",
        "err_no_ma": "Nessuna connessione a Music Assistant",
        "err_no_player": "sendspin-cli non raggiungibile",
        "err_pairing": "Codice di abbinamento MA: {pin}",
        "clock_last": "Ultimo: ",
        "clock_hint": "tocca per controllare",
        "calibrate_title": "Calibra il touch",
        "calibrate_step": "Tocca la croce con precisione ({n}/3).",
        "calibrate_done": "Calibrazione completata",
        "menu_title": "MENU",
        "menu_settings": "Impostazioni",
        "power_shutdown": "Spegni",
        "power_restart": "Riavvia",
        "power_cancel": "Annulla",
        "power_busy_shutdown": "Spegnimento…",
        "power_busy_restart": "Riavvio…",
        "power_wait_shutdown": "Attendi un momento prima di scollegare l'alimentazione.",
        "power_wait_restart": "Il display torna tra circa un minuto.",
        "power_failed": "Comando di alimentazione non riuscito",
        "status_starting": "AVVIO",
        "starting": "Avvio…",
        "tap_resume": "Tocca per riprendere",
        "date_short": "{day} {month}",
        "tab_rooms": "Stanze",
        "rooms_this": "Questo lettore",
        "rooms_offline": "offline",
        "rooms_none": "Nessun'altra stanza",
        "rooms_loading": "Caricamento stanze…",
        "rooms_failed": "Impossibile caricare le stanze",
        "room_add_q": "Aggiungere {name} al gruppo?",
        "room_remove_q": "Rimuovere {name} dal gruppo?",
        "room_add": "Aggiungi",
        "room_remove": "Rimuovi",
        "room_failed": "Impossibile modificare il gruppo",
        "room_done_add": "{name} aggiunto",
        "room_done_remove": "{name} rimosso",
        "tab_general": "Generale",
        "tab_display": "Schermo",
        "tab_info": "Info",
        "set_language": "Lingua",
        "set_cover": "Copertina",
        "set_analyser": "Analizzatore di spettro",
        "set_idle_style": "Schermata di riposo",
        "set_idle_after": "Riposo dopo",
        "set_night_after": "Modalità notte dopo",
        "set_volume_step": "Passo volume",
        "set_reset": "Ripristina predefiniti",
        "style_analog": "Analogico",
        "style_digital": "Digitale",
        "off": "Spento",
        "info_host": "Nome host",
        "info_ip": "Indirizzo IP",
        "info_uptime": "Tempo attivo",
        "info_ma": "Music Assistant",
        "info_temp": "Temperatura",
        "ma_connected": "connesso",
        "ma_disconnected": "non connesso",
        "settings_reset_done": "Impostazioni ripristinate",
        "settings_failed": "Salvataggio non riuscito",
        "date": "{weekday} {day} {month}",
        "weekdays": ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"],
        "months": ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"],
    },
    "pt": {
        "status_playing": "REPRODUZINDO",
        "status_paused": "PAUSADO",
        "status_idle": "INATIVO",
        "status_stopped": "PARADO",
        "status_off": "DESLIGADO",
        "status_unavailable": "INDISPONÍVEL",
        "live": "AO VIVO",
        "queue_empty": "Nada na fila",
        "player_unreachable": "Player inacessível",
        "resume_hint": "Último tocado · toque em play para retomar",
        "tab_fav": "Favoritas",
        "tab_all": "Todas",
        "stations_loading": "Carregando estações…",
        "stations_none": "Nenhuma estação",
        "stations_failed": "Não foi possível carregar as estações",
        "tap_retry": "Toque para tentar de novo",
        "volume": "VOLUME",
        "muted": "sem som",
        "failed": "Falhou",
        "play_failed": "Falha na reprodução",
        "resume_failed": "Não foi possível retomar",
        "retrying": "Tentando de novo…",
        "err_no_ma": "Sem conexão com o Music Assistant",
        "err_no_player": "sendspin-cli inacessível",
        "err_pairing": "Código de pareamento do MA: {pin}",
        "clock_last": "Último: ",
        "clock_hint": "toque para controlar",
        "calibrate_title": "Calibrar o toque",
        "calibrate_step": "Toque exatamente na cruz ({n}/3).",
        "calibrate_done": "Calibração concluída",
        "menu_title": "MENU",
        "menu_settings": "Configurações",
        "power_shutdown": "Desligar",
        "power_restart": "Reiniciar",
        "power_cancel": "Cancelar",
        "power_busy_shutdown": "Desligando…",
        "power_busy_restart": "Reiniciando…",
        "power_wait_shutdown": "Aguarde um momento antes de tirar da tomada.",
        "power_wait_restart": "O display volta em cerca de um minuto.",
        "power_failed": "Falha no comando de energia",
        "status_starting": "INICIANDO",
        "starting": "Iniciando…",
        "tap_resume": "Toque para retomar",
        "date_short": "{day} de {month}",
        "tab_rooms": "Salas",
        "rooms_this": "Este player",
        "rooms_offline": "offline",
        "rooms_none": "Nenhuma outra sala",
        "rooms_loading": "Carregando salas…",
        "rooms_failed": "Não foi possível carregar as salas",
        "room_add_q": "Adicionar {name} ao grupo?",
        "room_remove_q": "Remover {name} do grupo?",
        "room_add": "Adicionar",
        "room_remove": "Remover",
        "room_failed": "Não foi possível alterar o grupo",
        "room_done_add": "{name} adicionado",
        "room_done_remove": "{name} removido",
        "tab_general": "Geral",
        "tab_display": "Tela",
        "tab_info": "Info",
        "set_language": "Idioma",
        "set_cover": "Capa",
        "set_analyser": "Analisador de espectro",
        "set_idle_style": "Tela de espera",
        "set_idle_after": "Espera após",
        "set_night_after": "Modo noturno após",
        "set_volume_step": "Passo do volume",
        "set_reset": "Restaurar padrões",
        "style_analog": "Analógico",
        "style_digital": "Digital",
        "off": "Desligado",
        "info_host": "Nome do host",
        "info_ip": "Endereço IP",
        "info_uptime": "Tempo ligado",
        "info_ma": "Music Assistant",
        "info_temp": "Temperatura",
        "ma_connected": "conectado",
        "ma_disconnected": "desconectado",
        "settings_reset_done": "Padrões restaurados",
        "settings_failed": "Não foi possível salvar",
        "date": "{weekday}, {day} de {month}",
        "weekdays": ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"],
        "months": ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"],
    },
    "pl": {
        "status_playing": "ODTWARZANIE",
        "status_paused": "PAUZA",
        "status_idle": "NIEAKTYWNY",
        "status_stopped": "ZATRZYMANO",
        "status_off": "WYŁĄCZONY",
        "status_unavailable": "NIEDOSTĘPNY",
        "live": "NA ŻYWO",
        "queue_empty": "Kolejka jest pusta",
        "player_unreachable": "Odtwarzacz niedostępny",
        "resume_hint": "Ostatnio grane · naciśnij play, aby wznowić",
        "tab_fav": "Ulubione",
        "tab_all": "Wszystkie",
        "stations_loading": "Ładowanie stacji…",
        "stations_none": "Brak stacji",
        "stations_failed": "Nie udało się wczytać stacji",
        "tap_retry": "Dotknij, aby ponowić",
        "volume": "GŁOŚNOŚĆ",
        "muted": "wyciszono",
        "failed": "Błąd",
        "play_failed": "Błąd odtwarzania",
        "resume_failed": "Nie udało się wznowić",
        "retrying": "Ponawianie…",
        "err_no_ma": "Brak połączenia z Music Assistant",
        "err_no_player": "sendspin-cli niedostępny",
        "err_pairing": "Kod parowania MA: {pin}",
        "clock_last": "Ostatnio: ",
        "clock_hint": "dotknij, aby sterować",
        "calibrate_title": "Kalibracja dotyku",
        "calibrate_step": "Dotknij dokładnie krzyżyka ({n}/3).",
        "calibrate_done": "Kalibracja zakończona",
        "menu_title": "MENU",
        "menu_settings": "Ustawienia",
        "power_shutdown": "Wyłącz",
        "power_restart": "Uruchom ponownie",
        "power_cancel": "Anuluj",
        "power_busy_shutdown": "Wyłączanie…",
        "power_busy_restart": "Ponowne uruchamianie…",
        "power_wait_shutdown": "Poczekaj chwilę, zanim odłączysz zasilanie.",
        "power_wait_restart": "Wyświetlacz wróci za około minutę.",
        "power_failed": "Polecenie zasilania nie powiodło się",
        "status_starting": "URUCHAMIANIE",
        "starting": "Uruchamianie…",
        "tap_resume": "Dotknij, aby wznowić",
        "date_short": "{day} {month}",
        "tab_rooms": "Pokoje",
        "rooms_this": "Ten odtwarzacz",
        "rooms_offline": "offline",
        "rooms_none": "Brak innych pokoi",
        "rooms_loading": "Ładowanie pokoi…",
        "rooms_failed": "Nie udało się wczytać pokoi",
        "room_add_q": "Dodać {name} do grupy?",
        "room_remove_q": "Usunąć {name} z grupy?",
        "room_add": "Dodaj",
        "room_remove": "Usuń",
        "room_failed": "Nie udało się zmienić grupy",
        "room_done_add": "Dodano: {name}",
        "room_done_remove": "Usunięto: {name}",
        "tab_general": "Ogólne",
        "tab_display": "Ekran",
        "tab_info": "Info",
        "set_language": "Język",
        "set_cover": "Okładka",
        "set_analyser": "Analizator widma",
        "set_idle_style": "Ekran bezczynności",
        "set_idle_after": "Bezczynność po",
        "set_night_after": "Tryb nocny po",
        "set_volume_step": "Krok głośności",
        "set_reset": "Przywróć domyślne",
        "style_analog": "Analogowy",
        "style_digital": "Cyfrowy",
        "off": "Wył.",
        "info_host": "Nazwa hosta",
        "info_ip": "Adres IP",
        "info_uptime": "Czas pracy",
        "info_ma": "Music Assistant",
        "info_temp": "Temperatura",
        "ma_connected": "połączono",
        "ma_disconnected": "brak połączenia",
        "settings_reset_done": "Przywrócono domyślne",
        "settings_failed": "Nie udało się zapisać",
        "date": "{weekday}, {day} {month}",
        "weekdays": ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"],
        "months": ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia", "września", "października", "listopada", "grudnia"],
    },
    "sv": {
        "status_playing": "SPELAR NU",
        "status_paused": "PAUSAD",
        "status_idle": "INAKTIV",
        "status_stopped": "STOPPAD",
        "status_off": "AV",
        "status_unavailable": "OTILLGÄNGLIG",
        "live": "LIVE",
        "queue_empty": "Inget i kön",
        "player_unreachable": "Spelaren kan inte nås",
        "resume_hint": "Senast spelat · tryck på play för att återuppta",
        "tab_fav": "Favoriter",
        "tab_all": "Alla",
        "stations_loading": "Laddar stationer…",
        "stations_none": "Inga stationer",
        "stations_failed": "Kunde inte ladda stationer",
        "tap_retry": "Tryck för att försöka igen",
        "volume": "VOLYM",
        "muted": "ljud av",
        "failed": "Misslyckades",
        "play_failed": "Uppspelningen misslyckades",
        "resume_failed": "Kunde inte återuppta",
        "retrying": "Försöker igen…",
        "err_no_ma": "Ingen anslutning till Music Assistant",
        "err_no_player": "sendspin-cli kan inte nås",
        "err_pairing": "Parkopplingskod för MA: {pin}",
        "clock_last": "Senast: ",
        "clock_hint": "tryck för att styra",
        "calibrate_title": "Kalibrera pekskärmen",
        "calibrate_step": "Tryck exakt på korset ({n}/3).",
        "calibrate_done": "Kalibreringen klar",
        "menu_title": "MENY",
        "menu_settings": "Inställningar",
        "power_shutdown": "Stäng av",
        "power_restart": "Starta om",
        "power_cancel": "Avbryt",
        "power_busy_shutdown": "Stänger av…",
        "power_busy_restart": "Startar om…",
        "power_wait_shutdown": "Vänta en stund innan du drar ur strömmen.",
        "power_wait_restart": "Skärmen är tillbaka om ungefär en minut.",
        "power_failed": "Strömkommandot misslyckades",
        "status_starting": "STARTAR",
        "starting": "Startar…",
        "tap_resume": "Tryck för att återuppta",
        "date_short": "{day} {month}",
        "tab_rooms": "Rum",
        "rooms_this": "Den här spelaren",
        "rooms_offline": "offline",
        "rooms_none": "Inga andra rum",
        "rooms_loading": "Laddar rum…",
        "rooms_failed": "Kunde inte ladda rum",
        "room_add_q": "Lägga till {name} i gruppen?",
        "room_remove_q": "Ta bort {name} från gruppen?",
        "room_add": "Lägg till",
        "room_remove": "Ta bort",
        "room_failed": "Kunde inte ändra gruppen",
        "room_done_add": "{name} tillagd",
        "room_done_remove": "{name} borttagen",
        "tab_general": "Allmänt",
        "tab_display": "Skärm",
        "tab_info": "Info",
        "set_language": "Språk",
        "set_cover": "Omslag",
        "set_analyser": "Spektrumanalysator",
        "set_idle_style": "Viloläge",
        "set_idle_after": "Viloläge efter",
        "set_night_after": "Nattläge efter",
        "set_volume_step": "Volymsteg",
        "set_reset": "Återställ standard",
        "style_analog": "Analog",
        "style_digital": "Digital",
        "off": "Av",
        "info_host": "Värdnamn",
        "info_ip": "IP-adress",
        "info_uptime": "Drifttid",
        "info_ma": "Music Assistant",
        "info_temp": "Temperatur",
        "ma_connected": "ansluten",
        "ma_disconnected": "inte ansluten",
        "settings_reset_done": "Standard återställd",
        "settings_failed": "Kunde inte spara",
        "date": "{weekday} {day} {month}",
        "weekdays": ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"],
        "months": ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september", "oktober", "november", "december"],
    },
    "cs": {
        "status_playing": "PŘEHRÁVÁ SE",
        "status_paused": "POZASTAVENO",
        "status_idle": "NEČINNÝ",
        "status_stopped": "ZASTAVENO",
        "status_off": "VYPNUTO",
        "status_unavailable": "NEDOSTUPNÝ",
        "live": "ŽIVĚ",
        "queue_empty": "Ve frontě nic není",
        "player_unreachable": "Přehrávač není dostupný",
        "resume_hint": "Naposledy hráno · stiskněte play pro pokračování",
        "tab_fav": "Oblíbené",
        "tab_all": "Všechny",
        "stations_loading": "Načítání stanic…",
        "stations_none": "Žádné stanice",
        "stations_failed": "Stanice se nepodařilo načíst",
        "tap_retry": "Klepnutím to zkusíte znovu",
        "volume": "HLASITOST",
        "muted": "ztlumeno",
        "failed": "Chyba",
        "play_failed": "Přehrávání se nezdařilo",
        "resume_failed": "Pokračování se nezdařilo",
        "retrying": "Zkouším znovu…",
        "err_no_ma": "Bez spojení s Music Assistant",
        "err_no_player": "sendspin-cli není dostupný",
        "err_pairing": "Párovací kód pro MA: {pin}",
        "clock_last": "Naposledy: ",
        "clock_hint": "klepnutím ovládejte",
        "calibrate_title": "Kalibrace dotyku",
        "calibrate_step": "Klepněte přesně na křížek ({n}/3).",
        "calibrate_done": "Kalibrace dokončena",
        "menu_title": "MENU",
        "menu_settings": "Nastavení",
        "power_shutdown": "Vypnout",
        "power_restart": "Restartovat",
        "power_cancel": "Zrušit",
        "power_busy_shutdown": "Vypínání…",
        "power_busy_restart": "Restartování…",
        "power_wait_shutdown": "Chvíli počkejte, než odpojíte napájení.",
        "power_wait_restart": "Displej bude zpět asi za minutu.",
        "power_failed": "Příkaz napájení se nezdařil",
        "status_starting": "SPOUŠTÍ SE",
        "starting": "Spouštění…",
        "tap_resume": "Klepnutím pokračujte",
        "date_short": "{day}. {month}",
        "tab_rooms": "Místnosti",
        "rooms_this": "Tento přehrávač",
        "rooms_offline": "offline",
        "rooms_none": "Žádné další místnosti",
        "rooms_loading": "Načítání místností…",
        "rooms_failed": "Místnosti se nepodařilo načíst",
        "room_add_q": "Přidat {name} do skupiny?",
        "room_remove_q": "Odebrat {name} ze skupiny?",
        "room_add": "Přidat",
        "room_remove": "Odebrat",
        "room_failed": "Skupinu se nepodařilo změnit",
        "room_done_add": "{name} přidáno",
        "room_done_remove": "{name} odebráno",
        "tab_general": "Obecné",
        "tab_display": "Displej",
        "tab_info": "Info",
        "set_language": "Jazyk",
        "set_cover": "Obal alba",
        "set_analyser": "Analyzátor spektra",
        "set_idle_style": "Spořič",
        "set_idle_after": "Spořič po",
        "set_night_after": "Noční režim po",
        "set_volume_step": "Krok hlasitosti",
        "set_reset": "Obnovit výchozí",
        "style_analog": "Analogový",
        "style_digital": "Digitální",
        "off": "Vypnuto",
        "info_host": "Název hostitele",
        "info_ip": "Adresa IP",
        "info_uptime": "Doba provozu",
        "info_ma": "Music Assistant",
        "info_temp": "Teplota",
        "ma_connected": "připojeno",
        "ma_disconnected": "nepřipojeno",
        "settings_reset_done": "Výchozí obnoveno",
        "settings_failed": "Uložení se nezdařilo",
        "date": "{weekday} {day}. {month}",
        "weekdays": ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"],
        "months": ["ledna", "února", "března", "dubna", "května", "června", "července", "srpna", "září", "října", "listopadu", "prosince"],
    },
}
_lang = "en"


def set_language(code):
    """Pick the UI language: explicit config value, else $LANG (e.g. nl_NL.UTF-8), else English."""
    global _lang
    code = (code or os.environ.get("LC_ALL") or os.environ.get("LANG") or "en")[:2].lower()
    _lang = code if code in STRINGS else "en"
    return _lang


def tr(key, **kw):
    s = STRINGS[_lang].get(key)
    if s is None:
        s = STRINGS["en"][key]
    return s.format(**kw) if kw else s


def fmt_date(now):
    return tr("date", weekday=tr("weekdays")[now.weekday()], day=now.day, month=tr("months")[now.month - 1])


# Settings that can be changed on the screen (menu -> Settings). They are saved in
# state_dir/settings.json, which the service may write (it cannot write /etc/ma-display/config.json, and
# that file may hold the token), and they win over config.json. The last entry is an action, not a setting.
LANG_NAMES = {"en": "English", "nl": "Nederlands", "fr": "Français", "de": "Deutsch", "es": "Español",
              "it": "Italiano", "pt": "Português", "pl": "Polski", "sv": "Svenska", "cs": "Čeština"}
LANGUAGE_CODES = list(LANG_NAMES)
SETTING_DEFS = [
    {"tab": "general", "key": "language", "label": "set_language", "kind": "cycle", "values": LANGUAGE_CODES},
    {"tab": "general", "key": "volume_step", "label": "set_volume_step", "kind": "cycle", "values": [1, 2, 5, 10]},
    {"tab": "general", "label": "set_reset", "kind": "action"},
    {"tab": "display", "key": "show_cover", "label": "set_cover", "kind": "toggle"},
    {"tab": "display", "key": "show_analyser", "label": "set_analyser", "kind": "toggle"},
    {"tab": "display", "key": "idle_style", "label": "set_idle_style", "kind": "cycle", "values": ["analog", "digital"]},
    {"tab": "display", "key": "clock_after_min", "label": "set_idle_after", "kind": "step", "step": 1, "min": 1,
     "max": 60},
    {"tab": "display", "key": "night_after_min", "label": "set_night_after", "kind": "step", "step": 15, "min": 0,
     "max": 240},
]
SETTING_KEYS = tuple(sd["key"] for sd in SETTING_DEFS if "key" in sd)


def _valid_setting(key, value):
    sd = next((s for s in SETTING_DEFS if s.get("key") == key), None)
    if sd is None:
        return False
    if sd["kind"] == "toggle":
        return isinstance(value, bool)
    if isinstance(value, bool):  # True == 1 would otherwise pass for the numeric settings
        return False
    if sd["kind"] == "cycle":
        return value in sd["values"]
    return isinstance(value, (int, float)) and sd["min"] <= value <= sd["max"]


def settings_path(state_dir):
    return os.path.join(state_dir, "settings.json")


def load_settings(state_dir):
    """The saved settings; anything unknown or out of range is ignored."""
    try:
        with open(settings_path(state_dir)) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    good = {k: v for k, v in raw.items() if _valid_setting(k, v)} if isinstance(raw, dict) else {}
    if raw != good:
        log.warning("ignoring invalid entries in %s", settings_path(state_dir))
    return good


def save_settings(state_dir, settings):
    """Write the settings atomically; an empty dict removes the file. Returns False when that fails."""
    path = settings_path(state_dir)
    try:
        if not settings:
            if os.path.exists(path):
                os.remove(path)
            return True
        os.makedirs(state_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(settings, f, indent=2)
        os.replace(tmp, path)
        return True
    except OSError as e:
        log.warning("saving settings failed: %s", e)
        return False


def settings_rows(tab):
    """The rows of one settings tab."""
    return [sd for sd in SETTING_DEFS if sd["tab"] == tab]


def setting_text(sd, value):
    """How a setting's value reads on screen."""
    key = sd["key"]
    if key == "language":
        return LANG_NAMES.get(value, str(value))
    if key == "idle_style":
        return tr("style_" + value)
    if key == "night_after_min" and not value:
        return tr("off")
    if key in ("clock_after_min", "night_after_min"):
        return f"{value:g} min"
    return str(value)


def system_info(ma_connected):
    """(label key, value) lines for the Info tab."""
    ip = "-"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0] if s.getsockname()[0] != "0.0.0.0" else "-"
    except OSError:
        pass
    finally:
        s.close()
    try:
        with open("/proc/uptime") as f:
            up = int(float(f.read().split()[0]))
    except (OSError, ValueError):
        up = 0
    days, rest = divmod(up, 86400)
    hours, rest = divmod(rest, 3600)
    uptime = (f"{days} d " if days else "") + (f"{hours} h " if days or hours else "") + f"{rest // 60} min"
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            temp = f"{int(f.read()) / 1000:.0f} °C"
    except (OSError, ValueError):
        temp = "-"
    return [("info_host", socket.gethostname()), ("info_ip", ip), ("info_uptime", uptime),
            ("info_ma", tr("ma_connected") if ma_connected else tr("ma_disconnected")), ("info_temp", temp)]


def load_config(use_settings=True):
    cfg = copy.deepcopy(DEFAULTS)
    try:
        with open(CONFIG_PATH) as f:
            user = json.load(f)
        cal = user.pop("calibration", None)
        cfg.update(user)
        if cal:
            cfg["calibration"].update(cal)
    except FileNotFoundError:
        pass
    if use_settings:
        cfg.update(load_settings(cfg["state_dir"]))
    return cfg


def save_calibration(cal):
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    data["calibration"] = cal
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    # Keep the owner and mode of an existing file: it may hold the MA token (set-token makes it
    # root:sendspin-cli 0640), so a calibration must not make it world-readable.
    try:
        st = os.stat(CONFIG_PATH)
        os.chmod(tmp, st.st_mode & 0o777)
        if hasattr(os, "chown"):
            os.chown(tmp, st.st_uid, st.st_gid)
    except FileNotFoundError:
        os.chmod(tmp, 0o640)
    except PermissionError:
        pass  # not root: the file stays ours, which is the best we can do
    os.replace(tmp, CONFIG_PATH)


_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(FONT_BOLD if bold else FONT, size)
    return _fonts[key]


# ---------------------------------------------------------------- framebuffer

_LUT_R = [(v >> 3) << 3 for v in range(256)]
_LUT_GH = [v >> 5 for v in range(256)]
_LUT_GL = [((v >> 2) & 7) << 5 for v in range(256)]
_LUT_B = [v >> 3 for v in range(256)]


def to_rgb565(img):
    """RGB image -> little-endian RGB565 bytes (Pillow has no packer for this)."""
    r, g, b = img.split()
    hi = ImageChops.add(r.point(_LUT_R), g.point(_LUT_GH))
    lo = ImageChops.add(g.point(_LUT_GL), b.point(_LUT_B))
    return Image.merge("LA", (lo, hi)).tobytes()


def find_framebuffer(spec, name):
    """The /dev/fbN to draw on, or None if it is not there (yet)."""
    if spec != "auto":
        return spec if os.path.exists(spec) else None
    for sys_dir in sorted(glob.glob("/sys/class/graphics/fb[0-9]*")):
        try:
            with open(sys_dir + "/name") as f:
                if f.read().strip() == name:
                    return "/dev/" + os.path.basename(sys_dir)
        except OSError:
            pass
    return None


def open_framebuffer(cfg, wait=120):
    """Open the panel's framebuffer, waiting for the driver instead of dying: the service starts early."""
    deadline = time.time() + wait
    warned = failed = False
    while True:
        path = find_framebuffer(cfg["fb"], cfg["fb_name"])
        if path:
            try:
                fb = Framebuffer(path)
                log.info("drawing on %s", path)
                return fb
            except OSError as e:
                if not failed:  # e.g. a permission problem would otherwise look like "still waiting"
                    log.warning("cannot open %s yet: %s", path, e)
                    failed = True
        if time.time() > deadline:
            raise RuntimeError(f"no framebuffer '{cfg['fb']}' ({cfg['fb_name']}) after {wait} s")
        if not warned:
            log.info("waiting for the display framebuffer")
            warned = True
        time.sleep(0.5)


def wait_for_route(timeout=120):
    """Wait until the Pi has an IPv4 address (a UDP connect sends nothing but needs a route)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))
            if s.getsockname()[0] != "0.0.0.0":
                return True
        except OSError:
            pass
        finally:
            s.close()
        time.sleep(1)
    return False


class Framebuffer:
    # The service puts tty1 into graphics mode (ExecStartPre, as root) so the console
    # does not draw over the picture.

    def __init__(self, path):
        base = "/sys/class/graphics/" + os.path.basename(path)
        self.stride = int(open(base + "/stride").read())
        self.fd = os.open(path, os.O_RDWR)
        self._activate()

    def _activate(self):
        """Switch the panel's display pipeline on.

        When the panel driver loads before vc4 (early module loading), the console stays on the HDMI
        framebuffer and nothing ever enables the panel's DRM pipeline: we would draw into the buffer but
        the panel shows nothing. Re-applying the current video mode with FB_ACTIVATE_FORCE makes the
        driver do its modeset. Harmless when the pipeline is already on.
        """
        import fcntl
        try:
            buf = bytearray(160)  # struct fb_var_screeninfo
            fcntl.ioctl(self.fd, 0x4600, buf)  # FBIOGET_VSCREENINFO
            buf[84:88] = (int.from_bytes(buf[84:88], "little") | 0x80).to_bytes(4, "little")  # activate |= FORCE
            fcntl.ioctl(self.fd, 0x4601, buf)  # FBIOPUT_VSCREENINFO: set_par -> modeset
        except OSError as e:
            log.warning("could not activate the framebuffer: %s", e)

    def show(self, img):
        data = to_rgb565(img)
        if self.stride != W * 2:
            pad = bytes(self.stride - W * 2)
            data = b"".join(data[y * W * 2:(y + 1) * W * 2] + pad for y in range(H))
        os.pwrite(self.fd, data, 0)

    def show_region(self, img, x0, y0):
        """Update just a strip (scrolling text): far less data over SPI."""
        data = to_rgb565(img)
        row = img.width * 2
        for y in range(img.height):
            os.pwrite(self.fd, data[y * row:(y + 1) * row], (y0 + y) * self.stride + x0 * 2)

    def close(self):
        os.close(self.fd)


class PngSink:
    def __init__(self, path):
        self.path = path

    def show(self, img):
        img.save(self.path)

    def show_region(self, img, x0, y0):
        pass

    def close(self):
        pass


# ---------------------------------------------------------------- touch

class Touch(threading.Thread):
    """Reads the touch panel and puts taps (x, y) on a queue.

    A touch held still for HOLD_SEC also puts a long press (x, y, "hold") on the queue.
    """

    HOLD_SEC = 1.5   # how long a touch has to rest to count as a long press
    HOLD_SLOP = 200  # raw units the finger may drift meanwhile

    def __init__(self, name, cal, taps, raw_mode=False):
        super().__init__(daemon=True)
        import evdev
        self.ev = evdev
        self.dev = next((evdev.InputDevice(p) for p in evdev.list_devices()
                         if evdev.InputDevice(p).name == name), None)
        if self.dev is None:
            raise RuntimeError(f"touch device '{name}' not found")
        self.cal = cal
        self.taps = taps
        self.raw_mode = raw_mode

    def map(self, rx, ry):
        c = self.cal
        a, b = (ry, rx) if c["swap_xy"] else (rx, ry)
        x = (a - c["x_min"]) / (c["x_max"] - c["x_min"]) * W
        y = (b - c["y_min"]) / (c["y_max"] - c["y_min"]) * H
        return max(0, min(W - 1, int(x))), max(0, min(H - 1, int(y)))

    def run(self):
        import select
        e = self.ev.ecodes
        rx = ry = 0
        down = False
        pressed = False
        held = False
        t_down = 0.0
        start = (0, 0)
        samples = []
        while True:
            # Wake up regularly even when no events come in: a long press has to fire while the
            # finger rests, which is exactly when the controller may stay quiet.
            if select.select([self.dev.fd], [], [], 0.1)[0]:
                try:
                    events = list(self.dev.read())
                except BlockingIOError:
                    events = []
                for ev in events:
                    if ev.type == e.EV_ABS:
                        if ev.code == e.ABS_X:
                            rx = ev.value
                        elif ev.code == e.ABS_Y:
                            ry = ev.value
                    elif ev.type == e.EV_KEY and ev.code == e.BTN_TOUCH:
                        down = bool(ev.value)
                    elif ev.type == e.EV_SYN:
                        if down:
                            samples.append((rx, ry))
                            if not pressed and len(samples) >= 3:
                                pressed = True
                                # The first samples are often noisy: take the median of what we have.
                                xs = sorted(s[0] for s in samples)
                                ys = sorted(s[1] for s in samples)
                                start = (xs[len(xs) // 2], ys[len(ys) // 2])
                                t_down, held = time.time(), False
                                log.debug("tap raw=%s", start)
                                self.taps.put(start if self.raw_mode else self.map(*start))
                        else:
                            pressed = held = False
                            samples = []
            if (pressed and not held and not self.raw_mode and time.time() - t_down >= self.HOLD_SEC
                    and abs(rx - start[0]) <= self.HOLD_SLOP and abs(ry - start[1]) <= self.HOLD_SLOP):
                held = True
                log.debug("long press raw=%s", start)
                self.taps.put((*self.map(*start), "hold"))


# ---------------------------------------------------------------- sendspin-cli control socket

class PlayerSocket(threading.Thread):
    """Polls 'status' on the sendspin-cli control socket and sends commands."""

    def __init__(self, path, on_change, interval=1.0):
        super().__init__(daemon=True)
        self.path = path
        self.on_change = on_change
        self.interval = interval
        self.lock = threading.Lock()
        self.status = None  # dict, or None if the player is unreachable
        self.wake = threading.Event()

    def cmd(self, line, timeout=3.0):
        """Sends one command; returns (ok, text)."""
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(timeout)
        try:
            s.connect(self.path)
            s.sendall((line + "\n").encode())
            out = b""
            while chunk := s.recv(4096):
                out += chunk
        finally:
            s.close()
        text = out.decode(errors="replace")
        head, _, rest = text.partition("\n")
        return head.strip() == "ok", rest if head.strip() == "ok" else text.strip()

    def snapshot(self):
        with self.lock:
            return self.status

    def run(self):
        while True:
            try:
                ok, text = self.cmd("status")
                st = {}
                if ok:
                    for line in text.splitlines():
                        k, sep, v = line.partition(":")
                        if sep:
                            st[k.strip()] = v.strip()
                else:
                    st = None
            except OSError:
                st = None
            with self.lock:
                changed = st != self.status
                self.status = st
            if changed:
                self.on_change()
            self.wake.wait(self.interval)
            self.wake.clear()


# ---------------------------------------------------------------- Sendspin display client

class SendspinDisplay(threading.Thread):
    """Sendspin client with metadata/artwork/controller roles, in its own asyncio thread."""

    def __init__(self, cfg, on_change):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.on_change = on_change
        self.lock = threading.Lock()
        self.loop = None
        self.client = None
        self.connected = False
        self.meta = {}  # title, artist, album, duration_ms, progress_ms, speed, at
        self.cover = None
        self.group_state = None
        self.volume = None
        self.muted = None
        self.pin = None
        self.viz_enabled = True  # False: drop the spectrum frames (analyser switched off)
        from collections import deque
        self.viz = deque(maxlen=VIZ_FPS * 15)  # (playback moment µs, spectrum); ~8 s ahead

    def snapshot(self):
        with self.lock:
            return {"connected": self.connected, "meta": dict(self.meta), "cover": self.cover,
                    "group_state": self.group_state, "volume": self.volume,
                    "muted": self.muted, "pin": self.pin}

    def switch(self):
        """Ask the server to move this display to the next playing group."""
        from aiosendspin.models.types import MediaCommand
        client, loop = self.client, self.loop
        if client is not None and loop is not None:
            fut = asyncio.run_coroutine_threadsafe(client.send_group_command(MediaCommand.SWITCH), loop)
            fut.add_done_callback(lambda f: f.exception() and log.warning("switch failed: %s", f.exception()))

    def run(self):
        try:
            asyncio.run(self._main())
        except Exception:
            log.exception("Sendspin client stopped")

    def _identity(self):
        from aiosendspin.noise import Identity, b64url_decode, b64url_encode
        os.makedirs(self.cfg["state_dir"], exist_ok=True)
        path = os.path.join(self.cfg["state_dir"], "identity.json")
        try:
            with open(path) as f:
                return Identity.from_private_bytes(b64url_decode(json.load(f)["private_key"]))
        except FileNotFoundError:
            ident = Identity.generate()
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"private_key": b64url_encode(ident.private_bytes)}, f)
            return ident

    async def _main(self):
        from aiosendspin.client import ClientListener
        from aiosendspin.noise import FileClientPairingStore
        self.loop = asyncio.get_running_loop()
        self.identity = self._identity()
        self.store = await FileClientPairingStore.open(os.path.join(self.cfg["state_dir"], "pairing-store.json"))
        # Offer guest access: MA approves such a client automatically (like sendspin-cli);
        # without it the roles stay inactive until someone pairs it in MA.
        pcfg = await self.store.get_pairing_config()
        if not pcfg.unpaired_access_enabled:
            import dataclasses
            await self.store.store_pairing_config(dataclasses.replace(pcfg, unpaired_access_enabled=True))
            log.info("unpaired (guest) access enabled")
        listener = ClientListener(client_id=self.identity.peer_id, on_connection=self._on_connection,
                                  port=self.cfg["sendspin_port"], client_name=self.cfg["sendspin_name"])
        await listener.start()
        log.info("Sendspin display listening on port %d (id %s)", self.cfg["sendspin_port"], self.identity.peer_id)
        await asyncio.Event().wait()

    async def _show_pin(self, code):
        with self.lock:
            self.pin = code
        self.on_change()

    async def _on_connection(self, ws):
        from aiosendspin.client import PairingSupport, SendspinClient
        from aiosendspin.models.artwork import ArtworkChannel, ClientHelloArtworkSupport
        from aiosendspin.models.types import ArtworkSource, PictureFormat, Roles
        from aiosendspin.models.visualizer import ClientHelloVisualizerSpectrum, ClientHelloVisualizerSupport
        log.info("Music Assistant connecting")
        client = SendspinClient(
            identity=self.identity, client_name=self.cfg["sendspin_name"],
            roles=[Roles.METADATA, Roles.ARTWORK, Roles.CONTROLLER, Roles.VISUALIZER],
            pairing_store=self.store,
            pairing_support=PairingSupport(pin_display=self._show_pin, offer_static_pin=False),
            artwork_support=ClientHelloArtworkSupport(channels=[ArtworkChannel(
                source=ArtworkSource.ALBUM, format=PictureFormat.JPEG, media_width=184, media_height=184)]),
            # The server analyses the audio; we receive ready-made spectrum bands and beats.
            visualizer_support=ClientHelloVisualizerSupport(
                buffer_capacity=65536, rate_max=VIZ_FPS, types=["spectrum", "beat"],
                # mel instead of log: with log the lowest bands are narrower than the server's
                # FFT resolution and would always stay empty.
                spectrum=ClientHelloVisualizerSpectrum(n_disp_bins=VIZ_BINS, scale="mel", f_min=40, f_max=16000)),
        )
        client.add_metadata_listener(self._on_metadata)
        client.add_controller_state_listener(self._on_controller)
        client.add_group_update_listener(self._on_group)
        client.add_artwork_listener(self._on_artwork)
        client.add_visualizer_listener(lambda frames: self._on_visualizer(client, frames))
        # On stop/seek whatever was buffered ahead is no longer valid.
        client.add_stream_clear_listener(self._clear_viz)
        client.add_stream_end_listener(self._clear_viz)
        task = asyncio.create_task(client.attach_websocket(ws))
        try:
            async with asyncio.timeout(30):
                while not client.connected and not task.done():
                    await asyncio.sleep(0.05)
        except TimeoutError:
            task.cancel()
        if not client.connected:
            log.warning("Handshake with server failed")
            with contextlib.suppress(BaseException):
                await task
            return
        log.info("Connected to %s", client.server_info.name if client.server_info else "?")
        with self.lock:
            self.client, self.connected, self.pin = client, True, None
        self.on_change()
        try:
            with contextlib.suppress(Exception):
                await task
        finally:
            with self.lock:
                if self.client is client:
                    self.client, self.connected = None, False
                    self.meta, self.cover, self.group_state = {}, None, None
            log.info("Connection to server closed")
            self.on_change()

    def _on_metadata(self, payload):
        from aiosendspin.models.types import UndefinedField
        md = payload.metadata
        if isinstance(md, UndefinedField):
            return
        with self.lock:
            if md is None:
                self.meta = {}
                self.cover = None
            else:
                # Missing fields (UndefinedField) stay unchanged; None clears.
                for key in ("title", "artist", "album"):
                    v = getattr(md, key)
                    if not isinstance(v, UndefinedField):
                        self.meta[key] = v
                p = md.progress
                if not isinstance(p, UndefinedField):
                    if p is None:
                        for k in ("duration_ms", "progress_ms", "speed", "at"):
                            self.meta.pop(k, None)
                    else:
                        self.meta.update(duration_ms=p.track_duration, progress_ms=p.track_progress,
                                         speed=p.playback_speed, at=time.time())
        self.on_change()

    def _on_controller(self, payload):
        from aiosendspin.models.types import UndefinedField
        c = payload.controller
        if c is None or isinstance(c, UndefinedField):
            return
        with self.lock:
            self.volume, self.muted = c.volume, c.muted
        self.on_change()

    def _on_group(self, payload):
        if payload.playback_state is not None:
            with self.lock:
                self.group_state = payload.playback_state.value
            self.on_change()

    def _on_visualizer(self, client, frames):
        """Buffer frames keyed by the moment the sound is audible here (local clock, µs).

        MA sends them ~8 s ahead; current_spectrum() picks the frame that is sounding now.
        """
        if not self.viz_enabled:
            return
        with self.lock:
            for f in frames:
                if f.spectrum is not None:
                    self.viz.append((client.compute_play_time(f.timestamp_us), f.spectrum))
                    if os.environ.get("MA_DISPLAY_VIZDEBUG") and len(self.viz) % 20 == 0:
                        log.info("spectrum %s", f.spectrum)

    def _clear_viz(self, roles=None):
        if roles is None or "visualizer" in roles:
            with self.lock:
                self.viz.clear()

    def current_spectrum(self):
        """The spectrum that is audible now, or None if there is nothing (for a moment)."""
        client = self.client
        if client is None:
            return None
        now = client.now_us()
        with self.lock:
            current = None
            while self.viz and self.viz[0][0] <= now:
                current = self.viz.popleft()
            if current is not None:
                self._viz_last = current
            last = getattr(self, "_viz_last", None)
        if last is None or now - last[0] > 500_000:  # nothing for over 0.5 s: silence
            return None
        return last[1]

    def _on_artwork(self, channel, data):
        if channel != 0:
            return
        img = None
        if data:
            try:
                img = Image.open(io.BytesIO(data)).convert("RGB")
            except OSError as e:
                log.warning("artwork could not be decoded: %s", e)
        with self.lock:
            self.cover = img
        self.on_change()


# ---------------------------------------------------------------- Music Assistant API (picking stations)

class MAClient:
    """Fetches radio stations and starts them on the player through the MA websocket API."""

    LOGO = 72

    def __init__(self, cfg, on_change):
        self.url = cfg["ma_url"].rstrip("/")
        self.token = cfg["ma_token"]
        self.player_name = cfg["ma_player_name"]
        self.logo_dir = os.path.join(cfg["state_dir"], "logos")
        self.on_change = on_change
        self.lock = threading.Lock()
        self.stations = {"fav": [], "all": []}
        self.loaded_at = 0
        self.loading = False
        self.error = None
        self.player_id = None
        # Last played station/track, so 'play' can resume it after a player restart (empty queue).
        self.last_path = os.path.join(cfg["state_dir"], "last_played.json")
        self.last_logo_path = os.path.join(cfg["state_dir"], "last_logo.png")
        # The group of this player: who is in it and who could be (see refresh_rooms_async).
        self.rooms = {"leader": None, "rows": []}
        self.rooms_loaded_at = 0
        self.rooms_loading = False
        self.rooms_error = None
        self.remembering = False
        try:
            with open(self.last_path) as f:
                self.last = json.load(f)
        except (OSError, ValueError):
            self.last = None
        try:
            # One fixed object: Renderer.background() remembers the cover by identity.
            self.last_logo = Image.open(self.last_logo_path).convert("RGB") if self.last else None
        except OSError:
            self.last_logo = None

    def snapshot(self):
        with self.lock:
            return {"stations": self.stations, "loading": self.loading, "error": self.error,
                    "loaded": self.loaded_at > 0, "rooms": self.rooms, "rooms_loading": self.rooms_loading,
                    "rooms_error": self.rooms_error}

    def last_played(self):
        with self.lock:
            return self.last

    def set_last(self, uri, name, logo=None):
        item = {"uri": uri, "name": name}
        with self.lock:
            if logo is None:
                logo = next((s["logo"] for s in self.stations["all"] if s["uri"] == uri and s["logo"]), None)
            if self.last == item and (logo is None or self.last_logo is not None):
                return
            if self.last != item:
                self.last_logo = None  # never leave the previous station's logo behind
            if logo is not None:
                self.last_logo = logo
            self.last = item
            keep = self.last_logo
        try:
            os.makedirs(os.path.dirname(self.last_path), exist_ok=True)
            tmp = self.last_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(item, f)
            os.replace(tmp, self.last_path)
            if keep is not None:
                tmp = self.last_logo_path + ".tmp"
                keep.save(tmp, "PNG")
                os.replace(tmp, self.last_logo_path)
            elif os.path.exists(self.last_logo_path):
                os.remove(self.last_logo_path)
            log.info("last played: %s (%s)%s", name, uri, "" if keep is not None else " without logo")
        except OSError as e:
            log.warning("saving last played failed: %s", e)

    def last_logo_for(self):
        with self.lock:
            return self.last_logo

    def remember_async(self):
        """Asks MA what the player is playing right now and remembers it."""
        with self.lock:
            if self.remembering:
                return
            self.remembering = True

        def run():
            try:
                ws, call = self._session()
                try:
                    q = call("player_queues/get_active_queue", player_id=self.player_id) or {}
                finally:
                    ws.close()
                cur = q.get("current_item") or {}
                media = cur.get("media_item") or {}
                uri = media.get("uri") or cur.get("uri")
                if uri:
                    self.set_last(uri, media.get("name") or cur.get("name") or uri)
            except Exception as e:
                log.debug("fetching the active queue failed: %s", e)
            finally:
                with self.lock:
                    self.remembering = False
        threading.Thread(target=run, daemon=True).start()

    def refresh_rooms_async(self, max_age=4, force=False):
        with self.lock:
            if self.rooms_loading or (not force and time.time() - self.rooms_loaded_at < max_age):
                return
            self.rooms_loading = True
        threading.Thread(target=self._refresh_rooms, daemon=True).start()

    def _refresh_rooms(self):
        try:
            ws, call = self._session()
            try:
                players = call("players/all") or []
            finally:
                ws.close()
            rooms = self._build_rooms(players)
            with self.lock:
                self.rooms, self.rooms_error = rooms, None
        except Exception as e:
            log.warning("fetching rooms failed: %s", e)
            with self.lock:
                self.rooms_error = "load_failed"
        finally:
            with self.lock:
                self.rooms_loading = False
                self.rooms_loaded_at = time.time()  # also after an error: do not hammer MA
            self.on_change()

    def _build_rooms(self, players):
        """The leader and one row per player that is in its group or could be (displays excluded)."""
        by_id = {p.get("player_id"): p for p in players}
        leader = by_id.get(self.player_id)
        if leader is None:
            return {"leader": None, "rows": []}
        members = [i for i in (leader.get("group_childs") or []) if i != self.player_id]
        rows = []
        for pid in dict.fromkeys(members + list(leader.get("can_group_with") or [])):
            p = by_id.get(pid)
            if p is None or pid == self.player_id or p.get("type") == "display":
                continue
            rows.append({"id": pid, "name": p.get("display_name") or p.get("name") or pid,
                         "member": pid in members, "available": bool(p.get("available"))})
        rows.sort(key=lambda row: (not row["member"], not row["available"]))  # members first, offline last
        return {"leader": {"id": self.player_id, "name": leader.get("display_name") or leader.get("name")},
                "rows": rows}

    def set_member_async(self, player_id, add, on_done, on_retry=None):
        """Add a player to / remove it from this player's group."""
        def run():
            key = "player_ids_to_add" if add else "player_ids_to_remove"
            for attempt in range(self.RETRIES):
                try:
                    ws, call = self._session()
                    try:
                        call("players/cmd/set_members", target_player=self.player_id, **{key: [player_id]})
                    finally:
                        ws.close()
                except Exception as e:
                    log.warning("%s %s failed (attempt %d/%d): %s", "adding" if add else "removing", player_id,
                                attempt + 1, self.RETRIES, e)
                    if attempt < self.RETRIES - 1:
                        if on_retry:
                            on_retry()
                        time.sleep(self.RETRY_WAIT)
                    continue
                log.info("%s %s %s the group", "added" if add else "removed", player_id, "to" if add else "from")
                self.refresh_rooms_async(force=True)
                on_done(True)
                return
            on_done(False)
        threading.Thread(target=run, daemon=True).start()

    def _session(self):
        import itertools
        import websocket
        ws = websocket.create_connection(self.url.replace("http", "ws", 1) + "/ws", timeout=10)
        ws.recv()  # server info
        counter = itertools.count(1)

        def call(cmd, **args):
            mid = str(next(counter))
            ws.send(json.dumps({"message_id": mid, "command": cmd, "args": args}))
            while True:
                r = json.loads(ws.recv())
                if r.get("message_id") == mid:
                    if "error_code" in r:
                        raise RuntimeError(r.get("details") or r["error_code"])
                    return r.get("result")

        try:
            call("auth", token=self.token)
            if not self.player_id:
                for p in call("players/all"):
                    if (p.get("display_name") or p.get("name")) == self.player_name and p.get("type") != "display":
                        self.player_id = p["player_id"]
                        break
                if not self.player_id:
                    raise RuntimeError(f"player {self.player_name} not found in MA")
        except Exception:
            ws.close()
            raise
        return ws, call

    def _logo(self, item):
        imgs = (item.get("metadata") or {}).get("images") or []
        img = next((i for i in imgs if i.get("type") == "thumb"), imgs[0] if imgs else None)
        if not img:
            return None
        key = img.get("proxy_id") or str(abs(hash(img.get("path"))))
        path = os.path.join(self.logo_dir, key + ".png")
        try:
            return Image.open(path).convert("RGB")
        except OSError:
            pass
        import urllib.request
        urls = []
        if img.get("remotely_accessible") and str(img.get("path", "")).startswith("http"):
            urls.append(img["path"])
        if img.get("proxy_id"):
            urls.append(f"{self.url}/imageproxy/{img['proxy_id']}")
        for url in urls:
            try:
                req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.token})
                with urllib.request.urlopen(req, timeout=8) as r:
                    logo = Image.open(io.BytesIO(r.read())).convert("RGB")
                logo = ImageOps.fit(logo, (self.LOGO, self.LOGO), Image.LANCZOS)
                os.makedirs(self.logo_dir, exist_ok=True)
                logo.save(path)
                return logo
            except Exception as e:
                log.debug("logo %s not loaded: %s", url, e)
        return None

    def refresh_async(self, max_age=600, force=False):
        with self.lock:
            if self.loading or (not force and time.time() - self.loaded_at < max_age):
                return
            self.error = None
            self.loading = True
        threading.Thread(target=self._refresh, daemon=True).start()
        self.on_change()

    RETRIES = 3       # attempts per action
    RETRY_WAIT = 2.0  # seconds between attempts

    def _refresh(self):
        try:
            for attempt in range(self.RETRIES):
                try:
                    ws, call = self._session()
                    try:
                        fav = call("music/radios/library_items", favorite=True, limit=100)
                        allr = call("music/radios/library_items", limit=500)
                    finally:
                        ws.close()

                    def conv(items):
                        return [{"name": r["name"], "uri": r["uri"], "logo": self._logo(r)} for r in items]

                    stations = {"fav": conv(fav), "all": conv(allr)}
                    with self.lock:
                        self.stations, self.loaded_at, self.error = stations, time.time(), None
                    log.info("%d stations loaded (%d favourite)", len(stations["all"]), len(stations["fav"]))
                    return
                except Exception as e:
                    log.warning("fetching stations failed (attempt %d/%d): %s", attempt + 1, self.RETRIES, e)
                    if attempt < self.RETRIES - 1:
                        time.sleep(self.RETRY_WAIT)
            with self.lock:
                self.error = "load_failed"  # translated where it is drawn
        finally:
            with self.lock:
                self.loading = False
            self.on_change()

    def play_async(self, station, on_done, on_retry=None):
        def run():
            for attempt in range(self.RETRIES):
                try:
                    ws, call = self._session()
                    try:
                        call("player_queues/play_media", queue_id=self.player_id, media=station["uri"])
                    finally:
                        ws.close()
                except Exception as e:
                    log.warning("playing %s failed (attempt %d/%d): %s", station["name"], attempt + 1,
                                self.RETRIES, e)
                    if attempt < self.RETRIES - 1:
                        if on_retry:
                            on_retry()
                        time.sleep(self.RETRY_WAIT)
                    continue
                self.set_last(station["uri"], station["name"], station.get("logo"))
                on_done(True)
                return
            on_done(False)
        threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------- drawing

def ellipsize(draw, text, fnt, maxw):
    if draw.textlength(text, font=fnt) <= maxw:
        return text
    while text and draw.textlength(text + "…", font=fnt) > maxw:
        text = text[:-1]
    return text.rstrip() + "…"


def wrap(draw, text, fnt, maxw, max_lines):
    words, lines, cur = text.split(), [], ""
    for i, w in enumerate(words):
        test = (cur + " " + w).strip()
        if not cur or draw.textlength(test, font=fnt) <= maxw:
            cur = test
            continue
        if len(lines) == max_lines - 1:
            # Last line: append the rest so ellipsize adds the "…".
            cur = " ".join([cur] + words[i:])
            break
        lines.append(cur)
        cur = w
    if cur:
        lines.append(cur)
    return [ellipsize(draw, line, fnt, maxw) for line in lines]


def overflows(lines, text):
    """Did wrap() have to truncate the text?"""
    return bool(lines) and lines[-1].endswith("…") and not text.rstrip().endswith("…")


def fmt_time(sec):
    sec = max(0, int(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def rounded(img, radius):
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.size[0] - 1, img.size[1] - 1), radius, fill=255)
    return img, mask


def icon(d, kind, cx, cy, s, col):
    """Simple vector icons, s = half size."""
    if kind == "play":
        d.polygon([(cx - s * 0.55, cy - s), (cx - s * 0.55, cy + s), (cx + s, cy)], fill=col)
    elif kind == "pause":
        w = s * 0.38
        d.rectangle((cx - s * 0.75, cy - s, cx - s * 0.75 + w, cy + s), fill=col)
        d.rectangle((cx + s * 0.75 - w, cy - s, cx + s * 0.75, cy + s), fill=col)
    elif kind in ("prev", "next"):
        m = -1 if kind == "prev" else 1
        d.polygon([(cx - m * s * 0.8, cy - s * 0.8), (cx - m * s * 0.8, cy + s * 0.8), (cx + m * s * 0.45, cy)], fill=col)
        x = cx + m * s * 0.55
        d.rectangle((min(x, x + m * s * 0.25), cy - s * 0.8, max(x, x + m * s * 0.25), cy + s * 0.8), fill=col)
    elif kind == "radio":
        t = max(2, int(s * 0.16))
        d.line((cx - s * 0.55, cy - s * 0.5, cx + s * 0.45, cy - s * 1.05), fill=col, width=t)  # antenna
        d.rounded_rectangle((cx - s, cy - s * 0.5, cx + s, cy + s * 0.8), int(s * 0.25), outline=col, width=t)
        d.ellipse((cx - s * 0.72, cy - s * 0.22, cx - s * 0.08, cy + s * 0.5), outline=col, width=t)  # speaker
        for dy in (-0.12, 0.25):
            d.line((cx + s * 0.18, cy + s * dy, cx + s * 0.7, cy + s * dy), fill=col, width=t)
    elif kind == "gear":
        t = max(2, int(s * 0.22))
        for i in range(8):
            a = math.radians(i * 45)
            d.line((cx + s * 0.62 * math.cos(a), cy + s * 0.62 * math.sin(a), cx + s * math.cos(a), cy + s * math.sin(a)),
                   fill=col, width=t + 1)
        d.ellipse((cx - s * 0.7, cy - s * 0.7, cx + s * 0.7, cy + s * 0.7), outline=col, width=t)
        d.ellipse((cx - s * 0.25, cy - s * 0.25, cx + s * 0.25, cy + s * 0.25), fill=col)
    elif kind == "power":
        t = max(2, int(s * 0.18))
        d.arc((cx - s, cy - s, cx + s, cy + s), -55, 235, fill=col, width=t)
        d.line((cx, cy - s * 1.1, cx, cy - s * 0.1), fill=col, width=t)
    elif kind == "restart":
        t = max(2, int(s * 0.18))
        d.arc((cx - s, cy - s, cx + s, cy + s), 20, 320, fill=col, width=t)
        a = math.radians(320)  # arrow head at the end of the arc, pointing along the clockwise tangent
        px, py = cx + s * math.cos(a), cy + s * math.sin(a)
        tx, ty, nx, ny, k = -math.sin(a), math.cos(a), math.cos(a), math.sin(a), s * 0.6
        d.polygon([(px + tx * k * 1.1, py + ty * k * 1.1),
                   (px + nx * k * 0.8 - tx * k * 0.2, py + ny * k * 0.8 - ty * k * 0.2),
                   (px - nx * k * 0.8 - tx * k * 0.2, py - ny * k * 0.8 - ty * k * 0.2)], fill=col)
    elif kind == "close":
        t = max(2, int(s * 0.2))
        d.line((cx - s, cy - s, cx + s, cy + s), fill=col, width=t)
        d.line((cx - s, cy + s, cx + s, cy - s), fill=col, width=t)
    elif kind in ("left", "right"):
        m = -1 if kind == "left" else 1
        d.polygon([(cx - m * s * 0.5, cy - s), (cx - m * s * 0.5, cy + s), (cx + m * s * 0.6, cy)], fill=col)
    elif kind in ("vol-", "vol+", "mute", "muted"):
        ox = cx - s * 0.35
        d.polygon([(ox - s * 0.8, cy - s * 0.35), (ox - s * 0.35, cy - s * 0.35), (ox + s * 0.15, cy - s * 0.8),
                   (ox + s * 0.15, cy + s * 0.8), (ox - s * 0.35, cy + s * 0.35), (ox - s * 0.8, cy + s * 0.35)], fill=col)
        px, t = cx + s * 0.75, max(2, int(s * 0.16))
        if kind == "muted":
            r = s * 0.36
            d.line((px - r, cy - r, px + r, cy + r), fill=col, width=t)
            d.line((px - r, cy + r, px + r, cy - r), fill=col, width=t)
        elif kind == "mute":
            for i, r in enumerate((s * 0.35, s * 0.7)):
                d.arc((px - s * 0.55 - r, cy - r, px - s * 0.55 + r, cy + r), -50, 50, fill=col, width=t)
        else:
            d.rectangle((px - s * 0.38, cy - t / 2, px + s * 0.38, cy + t / 2), fill=col)
            if kind == "vol+":
                d.rectangle((px - t / 2, cy - s * 0.38, px + t / 2, cy + s * 0.38), fill=col)


# Bottom row: playback buttons centred, a small speaker icon on the right that opens the
# volume popup. Values: (x0, x1) of the tap area.
BTN_Y0, BTN_H = 232, 88
ROW_CY = BTN_Y0 + BTN_H // 2 - 4
MAIN_BUTTONS = {"stations": (0, 80), "prev": (100, 190), "play": (190, 290), "next": (290, 380),
                "volume": (400, 480)}
# Volume popup: panel and buttons as (x0, y0, x1, y1).
POPUP = (56, 44, 424, 268)
POPUP_BUTTONS = {"vol-": (80, 160, 180, 244), "mute": (190, 160, 290, 244), "vol+": (300, 160, 400, 244)}
POPUP_SECS = 4
MARQUEE_FPS = 15
VIZ_FPS = 20   # max. visualizer frames per second we ask the server for
VIZ_BINS = 24  # number of spectrum bands
# Floating glass bar behind the buttons
GLASS_BAR = (12, 238, 468, 308)
# Volume bar in the popup (x0, x1, y of the bar) and the larger tap area around it.
POPUP_BAR = (80, 400, 114)
POPUP_BAR_HIT = (64, 92, 416, 146)
# Power menu: long-press the clock (top-right corner of the player screen) to open it.
POWER_ZONE = (400, 0, 480, 40)
POWER_PANEL = (56, 44, 424, 268)
POWER_ROWS = {"settings": (80, 80, 400, 120), "restart": (80, 126, 400, 166), "shutdown": (80, 172, 400, 212),
              "cancel": (80, 218, 400, 258)}
POWER_SECS = 20  # the menu closes by itself after this long


def popup_bar_value(x, y):
    """Volume (0-100) if the bar was tapped, else None."""
    hx0, hy0, hx1, hy1 = POPUP_BAR_HIT
    if hx0 <= x < hx1 and hy0 <= y < hy1:
        bx0, bx1, _ = POPUP_BAR
        return round(min(1.0, max(0.0, (x - bx0) / (bx1 - bx0))) * 100)
    return None


def button_at(x, y):
    if y >= BTN_Y0 - 8:
        for name, (x0, x1) in MAIN_BUTTONS.items():
            if x0 <= x < x1:
                return name
    return None


# Station screen: 3x2 tiles, a header row with tabs, paging and close.
ST_COLS, ST_ROWS = 3, 2
ST_TILE_W, ST_TILE_H, ST_GAP = 144, 118, 8
ST_X0, ST_Y0 = 16, 56
ST_TAB_NAMES = ("fav", "all", "rooms")
ST_HEADER = {"left": (336, 368), "right": (404, 436), "close": (436, 480)}
STATIONS_SECS = 30
# Rooms tab: the players that can be in this player's group, one row each (a switch on the right).
ROOM_ROWS, ROOM_Y0, ROOM_ROW_H, ROOM_PITCH = 4, 100, 44, 48
ROOM_CONFIRM = (56, 62, 424, 258)
ROOM_CONFIRM_BUTTONS = {"yes": (80, 176, 236, 232), "no": (244, 176, 400, 232)}
# Settings screen: two tabs, then six rows (five settings and a reset action) or the system info.
SET_TAB_NAMES = ("general", "display", "info")
SET_Y0, SET_ROW_H, SET_ROW_PITCH = 56, 40, 43
SETTINGS_SECS = 60  # the screen closes by itself after this long without a tap


def tab_ranges(names):
    """Tab areas laid out by the width of their translated labels (12 px padding on both sides), so a long
    label in any language neither overlaps its neighbour nor leaves a tap area that does not match the text."""
    f, out, x = font(16, True), {}, 12
    for name in names:
        width = int(f.getlength(tr("tab_" + name))) + 24
        out[name] = (x, x + width)
        x += width
    return out


def stations_hit(x, y):
    """What was tapped on the station screen: ('tab', name) / ('left',) / ('tile', index) / ..."""
    if y < ST_Y0 - 4:
        for name, (x0, x1) in tab_ranges(ST_TAB_NAMES).items():
            if x0 <= x < x1:
                return ("tab", name)
        for name, (x0, x1) in ST_HEADER.items():
            if x0 <= x < x1:
                return (name,)
        return None
    c = (x - ST_X0) // (ST_TILE_W + ST_GAP)
    r = (y - ST_Y0) // (ST_TILE_H + ST_GAP)
    if 0 <= c < ST_COLS and 0 <= r < ST_ROWS:
        return ("tile", int(r * ST_COLS + c))
    return None


def rooms_row_at(x, y):
    """Row (0..ROOM_ROWS-1) of the rooms list at this point, or None."""
    if 16 <= x < 464 and ROOM_Y0 <= y < ROOM_Y0 + ROOM_ROWS * ROOM_PITCH and (y - ROOM_Y0) % ROOM_PITCH < ROOM_ROW_H:
        return (y - ROOM_Y0) // ROOM_PITCH
    return None


def confirm_button_at(x, y):
    for name, (x0, y0, x1, y1) in ROOM_CONFIRM_BUTTONS.items():
        if x0 <= x < x1 and y0 <= y < y1:
            return name
    return None


def settings_hit(x, y, n_rows):
    """What was tapped on the settings screen: ('tab', name) / ('close',) / ('row', index, part)."""
    if y < SET_Y0 - 4:
        for name, (x0, x1) in tab_ranges(SET_TAB_NAMES).items():
            if x0 <= x < x1:
                return ("tab", name)
        return ("close",) if x >= 428 else None
    i, within = divmod(y - SET_Y0, SET_ROW_PITCH)
    if not (0 <= i < n_rows and within < SET_ROW_H and 16 <= x < 464):
        return None
    part = "minus" if 288 <= x < 328 else "plus" if 416 <= x < 456 else "row"
    return ("row", i, part)


def picker_hit(x, y):
    """The language picker: a 2-column grid. Returns an index into LANGUAGE_CODES, 'close' or None."""
    if y < SET_Y0 - 4:
        return "close" if x >= 428 else None
    row, within = divmod(y - SET_Y0, 50)
    col = 0 if 16 <= x < 236 else 1 if 244 <= x < 464 else None
    if col is None or within >= 44 or row > 4 or row * 2 + col >= len(LANGUAGE_CODES):
        return None
    return row * 2 + col


def popup_button_at(x, y):
    for name, (x0, y0, x1, y1) in POPUP_BUTTONS.items():
        if x0 <= x < x1 and y0 <= y < y1:
            return name
    return None


def power_button_at(x, y):
    for name, (x0, y0, x1, y1) in POWER_ROWS.items():
        if x0 <= x < x1 and y0 <= y < y1:
            return name
    return None


def accent_from(cover):
    """Most striking colour of the cover, lifted to a bright, readable accent colour.

    Colours are grouped by hue; each pixel counts by saturation x brightness, so a large
    saturated area beats grey, black or white.
    """
    import colorsys
    if cover is None:
        return ACCENT_FALLBACK
    bins = {}
    for r, g, b in cover.convert("RGB").resize((24, 24)).getdata():
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s < 0.3 or v < 0.3:
            continue
        w = s * v
        acc = bins.setdefault(int(h * 12) % 12, [0.0, 0.0, 0.0, 0.0])
        acc[0] += w
        acc[1] += r * w
        acc[2] += g * w
        acc[3] += b * w
    if not bins:
        return ACCENT_FALLBACK
    total, rs, gs, bs = max(bins.values(), key=lambda a: a[0])
    if total < 8:  # not enough colour to build on
        return ACCENT_FALLBACK
    h, s, v = colorsys.rgb_to_hsv(rs / total / 255, gs / total / 255, bs / total / 255)
    r, g, b = colorsys.hsv_to_rgb(h, min(0.8, max(0.5, s)), max(0.9, v))
    col = (int(r * 255), int(g * 255), int(b * 255))
    # Lighten dark hues (deep blue, purple): the dark icon on the play button and the label on a
    # background of the same colour have to stay readable.
    for _ in range(10):
        if luminance(col) >= 0.4:
            break
        col = shade(col, 1.12)
    return col


def luminance(col):
    """Relative luminance (0-1) per WCAG."""
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = col
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def shade(col, f):
    """f < 1 darker, f > 1 lighter (towards white)."""
    if f <= 1:
        return tuple(int(c * f) for c in col)
    return tuple(int(c + (255 - c) * (f - 1)) for c in col)


def default_background():
    """Diagonal blue-purple gradient for when there is no cover (glass needs something to sit on)."""
    v = Image.linear_gradient("L").resize((W, H))
    g = ImageChops.add(v.point(lambda x: x // 2), v.transpose(Image.Transpose.ROTATE_90).resize((W, H)).point(lambda x: x // 2))
    a = Image.new("RGB", (W, H), (18, 26, 54))
    b = Image.new("RGB", (W, H), (58, 22, 62))
    return Image.composite(b, a, g)


class Marquee:
    """One line of text that scrolls horizontally when it is too long.

    The box is updated separately from the rest of the picture: first PAUSE seconds still
    at the start, then SPEED px/s to the left, with a second copy GAP px behind so the loop
    is seamless. The edges fade out softly.
    """

    PAUSE, SPEED, GAP, FADE = 2.0, 40, 56, 14
    _masks = {}

    def __init__(self, text, fnt, color, box, text_dy=2):
        self.text, self.font, self.color, self.box, self.text_dy = text, fnt, color, box, text_dy
        self.text_w = ImageDraw.Draw(Image.new("L", (1, 1))).textlength(text, font=fnt)
        self._alpha = None  # text (twice, with a gap) as a mask, rendered once
        self._bg_src = self._bg = self._fg = None

    def offset(self, t):
        period = self.PAUSE + (self.text_w + self.GAP) / self.SPEED
        phase = t % period
        return 0.0 if phase < self.PAUSE else (phase - self.PAUSE) * self.SPEED

    def _mask(self, w, h, left):
        key = (w, h, left)
        if key not in self._masks:
            ramp = [min(255, int(255 * min(x, w - 1 - x) / self.FADE)) for x in range(w)]
            if not left:  # at rest the text sits at the start: do not fade the left edge
                ramp = [255 if x < w // 2 else v for x, v in enumerate(ramp)]
            line = Image.new("L", (w, 1))
            line.putdata(ramp)
            self._masks[key] = line.resize((w, h))
        return self._masks[key]

    def render(self, base, t):
        """The strip for this box at time t (seconds since the text appeared).

        Rendering text is expensive on a Pi 2, so it is drawn once into a mask and each step
        only slides the window over it (all C operations inside Pillow).
        """
        x0, y0, x1, y1 = self.box
        w, h = x1 - x0, y1 - y0
        if self._bg_src is not base:
            self._bg_src, self._bg = base, base.crop(self.box)
            self._fg = Image.new("RGB", (w, h), self.color)
        if self._alpha is None:
            step = int(self.text_w + self.GAP)
            self._alpha = Image.new("L", (step + w + 2, h), 0)
            d = ImageDraw.Draw(self._alpha)
            for x in (0, step):
                d.text((x, self.text_dy), self.text, font=self.font, fill=255)
        off = int(self.offset(t))
        alpha = ImageChops.multiply(self._alpha.crop((off, 0, off + w, h)), self._mask(w, h, off > 0))
        return Image.composite(self._fg, self._bg, alpha)


class SpectrumViz:
    """Spectrum bars: fast up, slow back down (like a VU meter)."""

    DECAY = 1.6     # level drop per second
    # MA delivers 0-65535; for music the mel bands mostly sit between ~15000 and ~40000.
    # Stretching that range to 0-1 shows visible rhythm instead of a flat row.
    FLOOR, FULL = 12000, 40000

    def __init__(self, bins):
        self.levels = [0.0] * bins
        self.t = time.time()

    def update(self, spectrum):
        now = time.time()
        dt, self.t = min(0.2, now - self.t), now
        for i, v in enumerate(spectrum or [0] * len(self.levels)):
            if i >= len(self.levels):
                break
            target = min(1.0, max(0.0, (v - self.FLOOR) / (self.FULL - self.FLOOR)))
            self.levels[i] = target if target > self.levels[i] else max(target, self.levels[i] - self.DECAY * dt)

    @property
    def idle(self):
        return max(self.levels) < 0.01

    def render(self, base, box, color):
        x0, y0, x1, y1 = box
        strip = base.crop(box)
        d = ImageDraw.Draw(strip)
        w, h, n = x1 - x0, y1 - y0, len(self.levels)
        gap = 2
        bw = (w - gap * (n - 1)) / n
        top = shade(color, 1.35)
        for i, lvl in enumerate(self.levels):
            bh = max(2, int(lvl * h))
            bx = int(i * (bw + gap))
            d.rounded_rectangle((bx, h - bh, int(bx + bw) - 1, h - 1), 2, fill=color)
            if bh > 6:  # light cap: looks like glass
                d.rectangle((bx, h - bh, int(bx + bw) - 1, h - bh + 1), fill=top)
        return strip


class IdleClock:
    """The idle screen: an analog clock with the date and a tile for the last station.

    The face, date and tile are drawn once and cached (they only change with the date, the last station
    or the drift offset); each frame only draws the hands on a copy of the face area, so a frame costs a
    few milliseconds. The whole picture drifts slowly (about a pixel every few seconds), so no static
    image sits on the panel. After the night threshold it becomes a dim digital time that drifts further.
    """

    CENTER, R = (168, 160), 112
    SS = 2  # supersampling of the face and the hands (anti-aliasing)
    TEXT = (205, 205, 214)

    def __init__(self):
        self.key = None
        self.base = None       # the full picture without hands
        self.face = None       # the face area of `base`
        self.face_box = None   # where the face area sits on screen
        self.tile_box = None   # where the "last station" tile sits (tappable), or None
        self.animated = False  # does it need the hands drawn each frame?
        self.accent = ACCENT_FALLBACK
        self._gradients = {}

    @staticmethod
    def offset(t, scale=1):
        """Slow Lissajous drift of the whole picture, in pixels."""
        return (round(14 * scale * math.sin(t / 600 * 2 * math.pi)),
                round(8 * scale * math.sin(t / 420 * 2 * math.pi)))

    @staticmethod
    def _mix(a, b, t):
        return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

    def _gradient(self, top, bottom):
        if (top, bottom) not in self._gradients:
            g = Image.linear_gradient("L").resize((W, H))
            self._gradients[(top, bottom)] = Image.composite(
                Image.new("RGB", (W, H), bottom), Image.new("RGB", (W, H), top), g)
        return self._gradients[(top, bottom)]

    def render(self, now, last, logo, night, t=None):
        """The static picture for this moment; the very same object while nothing changed."""
        t = time.time() if t is None else t
        off = self.offset(t, 4 if night else 1)
        if night:
            key = ("night", off, now.hour, now.minute, _lang)
        else:
            key = ("day", off, now.date(), last["uri"] if last else None, id(logo), _lang)
        if key == self.key:
            return self.base
        self.key = key
        if night:
            img = self._gradient((4, 5, 9), (9, 7, 16)).copy()
            d = ImageDraw.Draw(img)
            cx, cy = 240 + off[0], 166 + off[1]
            d.text((cx, cy), now.strftime("%H:%M"), font=font(72, True), fill=(62, 62, 72), anchor="mm")
            d.text((cx, cy + 50), fmt_date(now), font=font(16), fill=(46, 46, 56), anchor="mm")
            self.base, self.animated, self.face_box, self.face, self.tile_box = img, False, None, None, None
            return img

        img = self._gradient((8, 10, 18), (20, 14, 34)).copy()
        self.accent = accent_from(logo) if logo is not None else ACCENT_FALLBACK
        # the colour of the text drifts with the time of day (0 .. 0.5 towards the accent colour)
        tint = 0.25 + 0.25 * math.sin((now.hour + now.minute / 60 - 6) / 24 * 2 * math.pi)
        text = self._mix(self.TEXT, self.accent, tint)
        dx, dy = off
        cx, cy = self.CENTER[0] + dx, self.CENTER[1] + dy
        S, Rr = self.SS, self.R
        size = (Rr + 8) * 2
        layer = Image.new("RGBA", (size * S, size * S), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        c = size * S // 2
        ld.ellipse((c - Rr * S, c - Rr * S, c + Rr * S, c + Rr * S), outline=(70, 70, 88, 255), width=3 * S)
        for i in range(60):
            a = math.radians(i * 6 - 90)
            major = i % 5 == 0
            r0, r1 = Rr * S - (14 if major else 6) * S, Rr * S - 4 * S
            ld.line((c + r0 * math.cos(a), c + r0 * math.sin(a), c + r1 * math.cos(a), c + r1 * math.sin(a)),
                    fill=(150, 150, 164, 255) if major else (80, 80, 96, 255), width=(3 if major else 1) * S)
        layer = layer.reduce(S)
        fx, fy = cx - size // 2, cy - size // 2
        img.paste(layer, (fx, fy), layer)

        d = ImageDraw.Draw(img)
        x0 = 300 + dx
        yw = (84 if last else 118) + dy
        weekday = tr("weekdays")[now.weekday()]
        date = tr("date_short", day=now.day, month=tr("months")[now.month - 1])
        wd_font = next((font(s, True) for s in (20, 19, 18, 17) if d.textlength(weekday, font=font(s, True)) <= 156),
                       font(17, True))
        d.text((x0, yw), ellipsize(d, weekday, wd_font, 156), font=wd_font, fill=text)
        d.text((x0, yw + 30), ellipsize(d, date, font(17), 156), font=font(17),
               fill=self._mix((120, 120, 132), self.accent, tint * 0.6))
        self.tile_box = None
        if last:
            box = (x0 - 4, 158 + dy, x0 + 154, 252 + dy)
            w, h = box[2] - box[0], box[3] - box[1]
            region = img.crop(box).convert("RGBA")
            ov = Image.new("RGBA", region.size, (0, 0, 0, 0))
            ImageDraw.Draw(ov).rounded_rectangle((0, 0, w - 1, h - 1), 14, fill=(255, 255, 255, 24),
                                                 outline=(255, 255, 255, 56))
            img.paste(Image.alpha_composite(region, ov).convert("RGB"), box[:2])
            d = ImageDraw.Draw(img)
            if logo is not None:
                lg, mask = rounded(ImageOps.fit(logo, (44, 44), Image.LANCZOS), 9)
                img.paste(lg, (box[0] + 8, box[1] + 14), mask)
            else:
                d.rounded_rectangle((box[0] + 8, box[1] + 14, box[0] + 52, box[1] + 58), 9, fill=(45, 45, 54))
                icon(d, "radio", box[0] + 30, box[1] + 38, 10, FG_FAINT)
            tx, tw = box[0] + 60, w - 60 - 6
            y = box[1] + 12
            for line in wrap(d, last["name"], font(14, True), tw, 2):
                d.text((tx, y), line, font=font(14, True), fill=FG)
                y += 17
            for line in wrap(d, tr("tap_resume"), font(12), tw, 2):
                d.text((tx, y + 3), line, font=font(12), fill=self._mix(FG_SOFT, self.accent, 0.5))
                y += 15
            self.tile_box = box
        self.base, self.animated = img, True
        self.face_box = (fx, fy, fx + size, fy + size)
        self.face = img.crop(self.face_box)
        return img

    def hands(self, now):
        """The face area with the hands for this moment (None when there is no face)."""
        if not self.animated:
            return None
        x0, _, x1, _ = self.face_box
        size, S = x1 - x0, self.SS
        layer = Image.new("RGBA", (size * S, size * S), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        c, Rr = size * S // 2, self.R * S
        ink, acc = self.TEXT + (255,), self.accent + (255,)

        def hand(angle, length, width, col, tail=0):
            a = math.radians(angle - 90)
            d.line((c - tail * math.cos(a), c - tail * math.sin(a), c + length * math.cos(a), c + length * math.sin(a)),
                   fill=col, width=width)

        sec = now.second + now.microsecond / 1e6
        minute = now.minute + sec / 60
        hour = now.hour % 12 + minute / 60
        hand(hour * 30, Rr * 0.52, 7 * S, ink)
        hand(minute * 6, Rr * 0.80, 5 * S, ink)
        hand(sec * 6, Rr * 0.88, 2 * S, acc, tail=22 * S)
        d.ellipse((c - 7 * S, c - 7 * S, c + 7 * S, c + 7 * S), fill=acc)
        layer = layer.reduce(S)
        out = self.face.copy()
        out.paste(layer, (0, 0), layer)
        return out


class Renderer:
    def __init__(self):
        self.bg_key = None
        self.bg = None
        self.frost = None
        self.cover_img = None
        self._glass = {}
        self.marquees = []
        self.viz_box = None
        self.set_accent(ACCENT_FALLBACK)

    def set_accent(self, col):
        self.accent = col
        self.accent_dark = shade(col, 0.85)
        self.accent_light = shade(col, 1.45)

    def background(self, cover):
        if cover is self.bg_key and self.bg is not None:
            return self.bg
        self.bg_key = cover
        if cover is None:
            self.bg = default_background()
            self.cover_img = None
        else:
            blur = ImageOps.fit(cover, (W // 4, H // 4)).filter(ImageFilter.GaussianBlur(6)).resize((W, H))
            blur = ImageEnhance.Color(blur).enhance(1.35)
            self.bg = Image.blend(blur, Image.new("RGB", (W, H), (0, 0, 0)), 0.55)
            # Light covers (e.g. black-and-white photos) give a mid-grey background that swallows
            # dimmed text: cap the mean brightness.
            mean = ImageStat.Stat(self.bg.convert("L")).mean[0]
            if mean > BG_MAX_LUMA:
                self.bg = Image.blend(self.bg, Image.new("RGB", (W, H), (0, 0, 0)), 1 - BG_MAX_LUMA / mean)
            self.cover_img = ImageOps.fit(cover, (184, 184), Image.LANCZOS)
        self.set_accent(accent_from(cover))
        # Frosted glass: blurred even more; at quarter resolution, which is cheap on a Pi 2.
        self.frost = self.bg.resize((W // 4, H // 4)).filter(ImageFilter.GaussianBlur(3)).resize((W, H))
        self._glass = {}
        return self.bg

    def glass(self, img, box, radius=16, alpha=0.10, light=False):
        """Glass panel: frosted-glass crop + white tint + shine on top + light edge.

        Computed once per background and position, afterwards only pasted.
        """
        key = (box, radius, alpha, light)
        cached = self._glass.get(key)
        if cached is None:
            x0, y0, x1, y1 = box
            w, h = x1 - x0, y1 - y0
            white = Image.new("RGB", (w, h), (255, 255, 255))
            panel = Image.blend(self.frost.crop(box), white, alpha + (0.10 if light else 0))
            shine = Image.linear_gradient("L").resize((w, h)).point(lambda v: max(0, 40 - v // 3))
            panel = Image.composite(white, panel, shine).convert("RGBA")
            edge = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(edge).rounded_rectangle((0, 0, w - 1, h - 1), radius, outline=(255, 255, 255, 80), width=1)
            panel = Image.alpha_composite(panel, edge).convert("RGB")
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius, fill=255)
            cached = self._glass[key] = (panel, mask)
        img.paste(cached[0], box[:2], cached[1])

    def player(self, data, cover, error, pressed=None, toast=None, show_stations=False, show_viz=True,
               show_cover=True):
        a = data.get("attributes", {}) if data else {}
        state = data.get("state", "unavailable") if data else "unavailable"
        img = self.background(cover).copy()
        d = ImageDraw.Draw(img)

        # Cover or placeholder. With the cover switched off the text takes the whole width, in bigger type.
        wide = not show_cover
        cx0, cy0 = 16, 16
        if wide:
            pass
        elif self.cover_img is not None:
            c, m = rounded(self.cover_img, 12)
            img.paste(c, (cx0, cy0), m)
        else:
            self.glass(img, (cx0, cy0, cx0 + 184, cy0 + 184), radius=12, alpha=0.06)
            icon(d, "play", cx0 + 92, cy0 + 92, 30, (150, 150, 170))

        # Text
        tx, tw = (24, W - 48) if wide else (216, W - 216 - 16)
        t_sz, t_lh, a_sz, a_lh, al_sz, al_lh = (28, 34, 20, 26, 15, 20) if wide else (24, 30, 17, 22, 14, 19)
        duration = a.get("media_duration") or 0
        is_live = duration == 0 and bool(a.get("media_title")) and a.get("media_duration") is not None
        label = tr("status_" + state) if ("status_" + state) in STRINGS["en"] else state.upper()
        # The label always states the status (also for radio); the LIVE badge next to it states the kind of source.
        d.text((tx, 18), label, font=font(13, True), fill=self.accent if state == "playing" else FG_DIM)
        left_end = tx + d.textlength(label, font=font(13, True))
        if is_live:
            # LIVE badge right next to the label (red while playing)
            bx = int(tx + d.textlength(label, font=font(13, True)) + 8)
            live = tr("live")
            lw = d.textlength(live, font=font(11, True))
            d.rounded_rectangle((bx, 16, bx + lw + 20, 33), 8, fill=ERROR if state == "playing" else (90, 90, 100))
            d.ellipse((bx + 5, 21, bx + 11, 27), fill=FG)
            d.text((bx + 14, 18), live, font=font(11, True), fill=FG)
            left_end = bx + lw + 20
        # Time on the right of the label row: same small type, dimmed, so it stays secondary. A long status in
        # some languages (plus the LIVE badge) leaves no room for it: then it is left out.
        now_txt = datetime.datetime.now().strftime("%H:%M")
        time_w = d.textlength(now_txt, font=font(13, True))
        if left_end + 12 <= tx + tw - time_w:
            d.text((tx + tw - time_w, 18), now_txt, font=font(13, True), fill=FG_SOFT)

        # Title and artist: if they do not fit on 2 lines, use one scrolling line (see Marquee).
        # The scrolling text itself is not drawn here but by App.animated_strips.
        self.marquees = []
        title = a.get("media_title") or (
            tr("starting") if state == "starting"
            else tr("queue_empty") if state != "unavailable" else tr("player_unreachable"))
        album = a.get("media_album_name")
        if not a.get("media_title") and a.get("resume_name") and state != "unavailable":
            # Empty queue (e.g. after a player restart): play resumes the last played item.
            title, album = a["resume_name"], tr("resume_hint")
        y = 44 if wide else 40
        lines = wrap(d, title, font(t_sz, True), tw, 2)
        if overflows(lines, title):
            self.marquees.append(Marquee(title, font(t_sz, True), FG, (tx, y - 2, tx + tw, y + t_lh + 2)))
            y += t_lh
        else:
            for line in lines:
                d.text((tx, y), line, font=font(t_sz, True), fill=FG)
                y += t_lh
        y += 4
        artist = a.get("media_artist")
        if artist:
            lines = wrap(d, artist, font(a_sz), tw, 2)
            if overflows(lines, artist):
                self.marquees.append(Marquee(artist, font(a_sz), FG_DIM, (tx, y - 2, tx + tw, y + a_lh)))
                y += a_lh
            else:
                for line in lines:
                    d.text((tx, y), line, font=font(a_sz), fill=FG_DIM)
                    y += a_lh
        if album and album != title:
            for line in wrap(d, album, font(al_sz), tw, 2 if not artist else 1):
                d.text((tx, y + 2), line, font=font(al_sz), fill=FG_SOFT)
                y += al_lh

        # Free space between the text and the progress bar/LIVE badge: the spectrum goes here.
        vtop = max(y + 10, 112)
        # For radio (no progress bar) the spectrum runs down to the bottom of the cover (y=200).
        vbot = 164 if duration > 0 else cy0 + 184
        self.viz_box = (tx, vtop, tx + tw, vbot) if show_viz and vbot - vtop >= 24 and state == "playing" else None

        # Progress or LIVE badge
        py = 172
        if duration > 0:
            pos = a.get("media_position") or 0
            upd = a.get("media_position_updated_at")
            if state == "playing" and upd:
                try:
                    t = datetime.datetime.fromisoformat(upd)
                    pos += (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds()
                except ValueError:
                    pass
            pos = min(pos, duration)
            d.rounded_rectangle((tx, py, tx + tw, py + 4), 2, fill=(70, 70, 80))
            d.rounded_rectangle((tx, py, tx + max(4, int(tw * pos / duration)), py + 4), 2, fill=FG)
            d.text((tx, py + 10), fmt_time(pos), font=font(12), fill=FG_DIM)
            rt = fmt_time(duration)
            d.text((tx + tw - d.textlength(rt, font=font(12)), py + 10), rt, font=font(12), fill=FG_DIM)
        elif is_live:
            # The LIVE badge sits at the top next to the label; this row is free for the spectrum.
            if show_viz and self.viz_box is None and state == "playing":
                self.viz_box = (tx, py - 4, tx + tw, py + 22)

        # Buttons on a floating glass bar
        muted = a.get("is_volume_muted")
        playing = state == "playing"
        cy = ROW_CY
        self.glass(img, GLASS_BAR, radius=22, alpha=0.08)
        for b, (x0, x1) in MAIN_BUTTONS.items():
            if b == "stations" and not show_stations:
                continue
            cx = (x0 + x1) // 2
            if pressed == b and b != "play":
                bx0, bx1 = max(x0, GLASS_BAR[0]) + 8, min(x1, GLASS_BAR[2]) - 8
                self.glass(img, (bx0, GLASS_BAR[1] + 8, bx1, GLASS_BAR[3] - 8), radius=14, alpha=0.08, light=True)
            if b == "play":
                # Glass circle with a white icon: stands out by size, not by colour.
                self.glass(img, (cx - 32, cy - 32, cx + 32, cy + 32), radius=32, alpha=0.2, light=pressed == b)
                icon(d, "pause" if playing else "play", cx + (0 if playing else 3), cy, 15, FG)
            elif b == "volume":
                # Deliberately small and dimmed; accent colour while muted.
                icon(d, "muted" if muted else "mute", cx, cy, 11, self.accent if muted else FG_SOFT)
            elif b == "stations":
                icon(d, "radio", cx, cy + 2, 11, FG_SOFT)
            else:
                icon(d, b, cx, cy, 14, FG)

        self._overlay(d, error, toast)
        return img

    def stations(self, snap, tab, page, current, pressed=None, toast=None, cover=None):
        """Station picker: tabs, 3x2 glass tiles with logo and name."""
        img = self.background(cover).copy()
        d = ImageDraw.Draw(img)
        for name, (x0, x1) in tab_ranges(ST_TAB_NAMES).items():
            label = tr("tab_" + name)
            active = name == tab
            f = font(16, True)
            tx = x0 + 12
            d.text((tx, 16), label, font=f, fill=FG if active else FG_FAINT)
            if active:
                d.rounded_rectangle((tx, 40, tx + d.textlength(label, font=f), 43), 2, fill=self.accent)
        rooms = tab == "rooms"
        items = snap["rooms"]["rows"] if rooms else snap["stations"].get(tab) or []
        per = ROOM_ROWS if rooms else ST_COLS * ST_ROWS
        pages = max(1, -(-len(items) // per))
        page = min(page, pages - 1)
        if pages > 1:
            (lx0, lx1), (rx0, rx1) = ST_HEADER["left"], ST_HEADER["right"]
            icon(d, "left", (lx0 + lx1) // 2, 26, 8, FG if page > 0 else (60, 60, 70))
            icon(d, "right", (rx0 + rx1) // 2, 26, 8, FG if page < pages - 1 else (60, 60, 70))
            pt = f"{page + 1}/{pages}"
            d.text(((lx1 + rx0) / 2 - d.textlength(pt, font=font(12)) / 2, 19), pt, font=font(12), fill=FG_SOFT)
        cx0, cx1 = ST_HEADER["close"]
        icon(d, "close", (cx0 + cx1) // 2, 26, 8, FG_DIM)

        if not items:
            if rooms:
                error = snap["rooms_error"]
                msg = tr("rooms_failed") if error else (
                    tr("rooms_loading") if snap["rooms_loading"] or snap["rooms"]["leader"] is None
                    else tr("rooms_none"))
            else:
                error = snap["error"]
                msg = tr("stations_failed") if error else (
                    tr("stations_loading") if snap["loading"] or not snap["loaded"] else tr("stations_none"))
            d.text(((W - d.textlength(msg, font=font(16))) / 2, 140), msg, font=font(16),
                   fill=ERROR if error else FG_DIM)
            if error:
                # Tapping anywhere in the empty area retries (see App.stations_tap).
                hint = tr("tap_retry")
                hw = d.textlength(hint, font=font(14, True))
                bx0, by0 = int((W - hw) / 2 - 18), 176
                self.glass(img, (bx0, by0, int(bx0 + hw + 36), by0 + 40), radius=20, alpha=0.1)
                d.text((bx0 + 18, by0 + 10), hint, font=font(14, True), fill=FG)
        if rooms:
            self._draw_rooms(img, d, snap, items, page, pressed)
            self._overlay(d, None, toast)
            return img, page
        cur = (current or "").casefold()
        for i, st in enumerate(items[page * per:(page + 1) * per]):
            r, c = divmod(i, ST_COLS)
            x = ST_X0 + c * (ST_TILE_W + ST_GAP)
            y = ST_Y0 + r * (ST_TILE_H + ST_GAP)
            playing_here = cur and st["name"].casefold() == cur
            self.glass(img, (x, y, x + ST_TILE_W, y + ST_TILE_H), radius=14, alpha=0.08,
                       light=pressed == page * per + i)
            if playing_here:
                d.rounded_rectangle((x, y, x + ST_TILE_W - 1, y + ST_TILE_H - 1), 14, outline=self.accent, width=2)
            lx, ly = x + (ST_TILE_W - MAClient.LOGO) // 2, y + 10
            if st["logo"] is not None:
                logo, m = rounded(st["logo"], 10)
                img.paste(logo, (lx, ly), m)
            else:
                d.rounded_rectangle((lx, ly, lx + MAClient.LOGO, ly + MAClient.LOGO), 10, fill=(45, 45, 54))
                icon(d, "radio", lx + MAClient.LOGO // 2, ly + MAClient.LOGO // 2 + 4, 18, FG_FAINT)
            name = ellipsize(d, st["name"], font(13, True), ST_TILE_W - 14)
            d.text((x + (ST_TILE_W - d.textlength(name, font=font(13, True))) / 2, y + 92), name,
                   font=font(13, True), fill=self.accent if playing_here else FG)
        self._overlay(d, None, toast)
        return img, page

    def _draw_rooms(self, img, d, snap, items, page, pressed):
        """This player on top, then the players that are (or could be) in its group, each with a switch."""
        leader = snap["rooms"]["leader"]
        if leader:
            d.ellipse((24, 70, 34, 80), fill=self.accent)
            name = ellipsize(d, leader["name"], font(15, True), 260)
            d.text((44, 64), name, font=font(15, True), fill=FG)
            d.text((44 + d.textlength(name, font=font(15, True)) + 10, 66), tr("rooms_this"), font=font(13),
                   fill=FG_FAINT)
        for i, row in enumerate(items[page * ROOM_ROWS:(page + 1) * ROOM_ROWS]):
            y = ROOM_Y0 + i * ROOM_PITCH
            self.glass(img, (16, y, 464, y + ROOM_ROW_H), radius=12, alpha=0.08, light=pressed == page * ROOM_ROWS + i)
            off = not row["available"]
            d.text((34, y + 11), ellipsize(d, row["name"], font(16, True), 270), font=font(16, True),
                   fill=FG_FAINT if off else FG)
            sx, sy = 464 - 18 - 52, y + 9
            if off:
                label = tr("rooms_offline")
                d.text((sx - 14 - d.textlength(label, font=font(13)), y + 14), label, font=font(13), fill=FG_FAINT)
            on = row["member"]
            d.rounded_rectangle((sx, sy, sx + 52, sy + 26), 13,
                                fill=(45, 45, 54) if off else (self.accent if on else (70, 70, 82)))
            kx = sx + 52 - 14 if on else sx + 14
            d.ellipse((kx - 10, sy + 3, kx + 10, sy + 23), fill=(110, 110, 120) if off else FG)

    def room_confirm(self, img, name, add, pressed=None):
        """Confirmation over the rooms list: add/remove a player."""
        img = Image.blend(img, Image.new("RGB", (W, H), (0, 0, 0)), 0.35)
        d = ImageDraw.Draw(img)
        x0, y0, x1, y1 = ROOM_CONFIRM
        self.glass(img, ROOM_CONFIRM, radius=20, alpha=0.14)
        y = y0 + 26
        for line in wrap(d, tr("room_add_q" if add else "room_remove_q", name=name), font(19, True), x1 - x0 - 48, 3):
            d.text((x0 + 24, y), line, font=font(19, True), fill=FG)
            y += 26
        for key, box in ROOM_CONFIRM_BUTTONS.items():
            if key == "yes":
                d.rounded_rectangle(box, 14, fill=self.accent_dark if pressed == key else self.accent)
                label, col = tr("room_add" if add else "room_remove"), (20, 20, 24)
            else:
                self.glass(img, box, radius=14, alpha=0.18, light=pressed == key)
                label, col = tr("power_cancel"), FG
            d.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), label, font=font(17, True), fill=col, anchor="mm")
        return img

    def volume_popup(self, img, data, pressed=None):
        """Volume panel on top of the player screen."""
        a = data.get("attributes", {}) if data else {}
        vol = a.get("volume_level") or 0
        muted = bool(a.get("is_volume_muted"))
        img = Image.blend(img, Image.new("RGB", (W, H), (0, 0, 0)), 0.35)
        d = ImageDraw.Draw(img)
        x0, y0, x1, y1 = POPUP
        self.glass(img, POPUP, radius=20, alpha=0.14)
        d.text((x0 + 24, y0 + 22), tr("volume"), font=font(13, True), fill=FG_DIM)
        txt = tr("muted") if muted else f"{round(vol * 100)}%"
        f = font(30, True)
        d.text((x1 - 24 - d.textlength(txt, font=f), y0 + 10), txt, font=f, fill=FG_FAINT if muted else FG)
        bx0, bx1, by = POPUP_BAR
        d.rounded_rectangle((bx0, by - 6, bx1, by + 6), 6, fill=(60, 60, 70))
        kx = bx0 + int((bx1 - bx0) * vol)
        if not muted:
            d.rounded_rectangle((bx0, by - 6, max(bx0 + 12, kx), by + 6), 6, fill=self.accent)
        # Knob on the bar: shows that you can tap it
        d.ellipse((kx - 11, by - 11, kx + 11, by + 11), fill=FG_FAINT if muted else FG)
        for name, (px0, py0, px1, py1) in POPUP_BUTTONS.items():
            active = name == "mute" and muted
            if active:
                d.rounded_rectangle((px0, py0, px1, py1), 14, fill=self.accent_dark if pressed == name else self.accent)
            else:
                self.glass(img, (px0, py0, px1, py1), radius=14, alpha=0.18, light=pressed == name)
            kind = {"vol-": "vol-", "vol+": "vol+", "mute": "muted" if muted else "mute"}[name]
            icon(d, kind, (px0 + px1) // 2, (py0 + py1) // 2, 18, (20, 20, 24) if active else FG)
        return img

    def power_menu(self, img, pressed=None):
        """Power menu on top of the player screen: shut down, restart or cancel."""
        img = Image.blend(img, Image.new("RGB", (W, H), (0, 0, 0)), 0.35)
        d = ImageDraw.Draw(img)
        x0, y0, x1, y1 = POWER_PANEL
        self.glass(img, POWER_PANEL, radius=20, alpha=0.14)
        d.text((x0 + 24, y0 + 14), tr("menu_title"), font=font(13, True), fill=FG_DIM)
        for name, (bx0, by0, bx1, by1) in POWER_ROWS.items():
            self.glass(img, (bx0, by0, bx1, by1), radius=14, alpha=0.18, light=pressed == name)
            col = ERROR if name == "shutdown" else FG
            kind = {"settings": "gear", "shutdown": "power", "restart": "restart", "cancel": "close"}[name]
            icon(d, kind, bx0 + 34, (by0 + by1) // 2, 13, col)
            label = tr("menu_settings") if name == "settings" else tr("power_" + name)
            d.text((bx0 + 64, (by0 + by1) / 2 - 12), label, font=font(18, True), fill=col)
        return img

    def settings_screen(self, cfg, tab, pressed, info, toast=None):
        """Settings: tabs (Settings / Info), then the setting rows or the system info."""
        img = self.background(None).copy()
        d = ImageDraw.Draw(img)
        for name, (x0, x1) in tab_ranges(SET_TAB_NAMES).items():
            label, f = tr("tab_" + name), font(16, True)
            d.text((x0 + 12, 16), label, font=f, fill=FG if name == tab else FG_FAINT)
            if name == tab:
                d.rounded_rectangle((x0 + 12, 40, x0 + 12 + d.textlength(label, font=f), 43), 2, fill=self.accent)
        cx0, cx1 = ST_HEADER["close"]
        icon(d, "close", (cx0 + cx1) // 2, 26, 8, FG_DIM)
        if tab == "info":
            self.glass(img, (16, SET_Y0, 464, SET_Y0 + len(info) * 44), radius=14, alpha=0.08)
            for i, (key, value) in enumerate(info):
                y = SET_Y0 + i * 44
                d.text((34, y + 13), tr(key), font=font(14), fill=FG_DIM)
                text = ellipsize(d, value, font(15, True), 250)
                d.text((446 - d.textlength(text, font=font(15, True)), y + 12), text, font=font(15, True), fill=FG)
        else:
            for i, sd in enumerate(settings_rows(tab)):
                y = SET_Y0 + i * SET_ROW_PITCH
                self.glass(img, (16, y, 464, y + SET_ROW_H), radius=12, alpha=0.08, light=pressed == i)
                action = sd["kind"] == "action"
                d.text((34, y + 9), ellipsize(d, tr(sd["label"]), font(16, True), 410 if action else 250),
                       font=font(16, True), fill=FG_DIM if action else FG)
                if sd["kind"] == "cycle":
                    text = setting_text(sd, cfg[sd["key"]])
                    d.text((444 - d.textlength(text, font=font(16, True)), y + 9), text, font=font(16, True),
                           fill=self.accent)
                elif sd["kind"] == "toggle":
                    on = cfg[sd["key"]]
                    sx, sy = 464 - 18 - 52, y + (SET_ROW_H - 26) // 2
                    d.rounded_rectangle((sx, sy, sx + 52, sy + 26), 13, fill=self.accent if on else (70, 70, 82))
                    kx = sx + 52 - 14 if on else sx + 14
                    d.ellipse((kx - 10, sy + 3, kx + 10, sy + 23), fill=FG)
                elif sd["kind"] == "step":
                    for symbol, bx in (("−", 288), ("+", 416)):
                        self.glass(img, (bx, y + 5, bx + 40, y + SET_ROW_H - 5), radius=10, alpha=0.16)
                        d.text((bx + 20, y + SET_ROW_H // 2), symbol, font=font(20, True), fill=FG, anchor="mm")
                    d.text((372, y + SET_ROW_H // 2), setting_text(sd, cfg[sd["key"]]), font=font(15, True),
                           fill=self.accent, anchor="mm")
        self._overlay(d, None, toast)
        return img

    def language_picker(self, active, pressed=None):
        """The settings screen's language list: every language by its own name, the active one marked."""
        img = self.background(None).copy()
        d = ImageDraw.Draw(img)
        label, f = tr("set_language"), font(16, True)
        d.text((24, 16), label, font=f, fill=FG)
        d.rounded_rectangle((24, 40, 24 + d.textlength(label, font=f), 43), 2, fill=self.accent)
        cx0, cx1 = ST_HEADER["close"]
        icon(d, "close", (cx0 + cx1) // 2, 26, 8, FG_DIM)
        for i, code in enumerate(LANGUAGE_CODES):
            x, y = (16, 244)[i % 2], SET_Y0 + (i // 2) * 50
            on = code == active
            self.glass(img, (x, y, x + 220, y + 44), radius=12, alpha=0.12 if on else 0.08, light=pressed == i)
            if on:
                d.rounded_rectangle((x, y, x + 219, y + 43), 12, outline=self.accent, width=2)
            d.text((x + 18, y + 11), LANG_NAMES[code], font=font(17, True), fill=self.accent if on else FG)
        return img

    def clock(self, data, error, toast=None):
        img = Image.new("RGB", (W, H), (0, 0, 0))
        d = ImageDraw.Draw(img)
        now = datetime.datetime.now()
        t = now.strftime("%H:%M")
        f = font(112, True)
        d.text(((W - d.textlength(t, font=f)) / 2, 58), t, font=f, fill=(200, 200, 208))
        ds = fmt_date(now)
        d.text(((W - d.textlength(ds, font=font(22))) / 2, 196), ds, font=font(22), fill=FG_FAINT)
        a = data.get("attributes", {}) if data else {}
        if a.get("media_title"):
            s = ellipsize(d, tr("clock_last") + a["media_title"], font(15), W - 60)
            d.text(((W - d.textlength(s, font=font(15))) / 2, 262), s, font=font(15), fill=(80, 80, 90))
        hint = tr("clock_hint")
        d.text(((W - d.textlength(hint, font=font(12))) / 2, 290), hint, font=font(12), fill=(60, 60, 68))
        self._overlay(d, error, toast)
        return img

    def message(self, title, lines):
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        d.text((24, 28), title, font=font(24, True), fill=self.accent)
        y = 76
        for line in lines:
            for part in wrap(d, line, font(16), W - 48, 3):
                d.text((24, y), part, font=font(16), fill=FG_DIM)
                y += 22
            y += 8
        return img

    def _overlay(self, d, error, toast):
        if error:
            tw = d.textlength(error, font=font(12, True))
            d.rounded_rectangle((W - tw - 34, 6, W - 6, 26), 10, fill=ERROR)
            d.text((W - tw - 20, 9), error, font=font(12, True), fill=FG)
        if toast:
            tw = d.textlength(toast, font=font(15, True))
            x0 = (W - tw) / 2 - 16
            d.rounded_rectangle((x0, 128, x0 + tw + 32, 162), 17, fill=(30, 30, 36), outline=self.accent, width=2)
            d.text((x0 + 16, 136), toast, font=font(15, True), fill=FG)


# ---------------------------------------------------------------- app

def _iso_at(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()


DEMO_STATE = {
    "state": "playing",
    "attributes": {
        "media_title": "Bohemian Rhapsody", "media_artist": "Queen",
        "media_album_name": "A Night at the Opera (2011 Remaster)",
        "media_duration": 355, "media_position": 121, "media_position_updated_at": _iso_at(time.time()),
        "volume_level": 0.76, "is_volume_muted": False,
    },
}


def demo_cover():
    img = Image.new("RGB", (300, 300))
    d = ImageDraw.Draw(img)
    for y in range(300):
        d.line([(0, y), (300, y)], fill=(120 + y // 3, 40 + y // 5, 90 - y // 6))
    d.ellipse((60, 60, 240, 240), outline=(255, 220, 160), width=10)
    return img


def _blank(v):
    return v if v and v.lower() not in ("none", "-", "unknown", "(none)") else None


def same_track(meta, sock_title):
    """Does the Sendspin metadata belong to what sendspin-cli is playing? It sometimes reports 'ARTIST - Title'."""
    title = (meta.get("title") or "").casefold().strip()
    s = (sock_title or "").casefold().strip()
    if not title:
        return False
    return not s or s == title or s.endswith(" - " + title) or title in s


class App:
    # Seconds the player plays while the display shows something else, before it sends 'switch'.
    SWITCH_AFTER = 6
    # The service starts before sendspin-cli is up. For this long an unreachable player reads
    # as "Starting…" rather than as an error.
    STARTUP_SECS = 90

    def __init__(self, cfg, sink, demo=False):
        self.cfg = cfg
        self.sink = sink
        self.demo = demo
        self.r = Renderer()
        self.taps = queue.Queue()
        self.dirty = threading.Event()
        self.toast = None
        self.toast_until = 0
        self.pressed = None
        self.pressed_until = 0
        self.last_active = time.time()
        self.started = time.time()
        self.awake_until = 0
        self.popup_until = 0
        self.power_until = 0
        self.power_busy = None  # "shutdown" / "restart" once chosen
        self.idle = IdleClock()
        self.settings = load_settings(cfg["state_dir"])  # what was changed on screen (also in cfg)
        self.base_cfg = load_config(use_settings=False)  # what "reset to defaults" goes back to
        self.settings_until = 0
        self.set_tab = "general"
        self.set_picker = False  # the language list is open
        self.room_confirm = None  # {"id", "name", "add"} while the add/remove confirmation is open
        self.room_confirm_until = 0
        self.idle_anim = None  # the IdleClock while its hands have to be redrawn, else None
        self.stations_until = 0
        self.marquee_t0 = {}
        self.spectrum = SpectrumViz(VIZ_BINS)
        self.st_tab = None
        self.st_page = 0
        self.optimistic = {}
        self.optimistic_until = 0
        self.mismatch_since = None
        self.last_switch = 0
        self.remembered = (None, 0)  # (title, time) of the last remember_async
        self._demo_cover = demo_cover() if demo else None
        if not demo:
            self.sock = PlayerSocket(cfg["control_socket"], self.dirty.set)
            self.ss = SendspinDisplay(cfg, self.dirty.set)
            self.ss.viz_enabled = cfg["show_analyser"]
        # Picking stations needs an MA token.
        self.ma = MAClient(cfg, self.dirty.set) if cfg["ma_token"] and cfg["ma_url"] and not demo else None
        if cfg["ma_token"] and not cfg["ma_url"] and not demo:
            log.warning("ma_token is set but ma_url is not: station picker disabled")

    # -- merge state into a single dict (same shape as an HA media_player)
    def snapshot(self):
        """Returns (data, error label, cover)."""
        if self.demo:
            return copy.deepcopy(DEMO_STATE), None, self._demo_cover
        st = self.sock.snapshot()
        ss = self.ss.snapshot()
        if st is None:
            if time.time() - self.started < self.STARTUP_SECS:
                return {"state": "starting", "attributes": {}}, None, None
            return {"state": "unavailable", "attributes": {}}, tr("err_no_player"), None
        state = (st.get("state") or "idle").split()[0].lower()
        sock_title = _blank(st.get("track"))
        m = ss["meta"]
        rich = ss["connected"] and same_track(m, sock_title)
        a = {}
        if rich:
            a["media_title"] = m.get("title")
            a["media_artist"] = m.get("artist")
            a["media_album_name"] = m.get("album")
            if "duration_ms" in m:
                a["media_duration"] = m["duration_ms"] / 1000
                a["media_position"] = m["progress_ms"] / 1000
                a["media_position_updated_at"] = _iso_at(m["at"])
            cover = ss["cover"]
        else:
            a["media_title"] = sock_title
            cover = None
            # No Sendspin metadata yet (just started): for radio, show the remembered station name already.
            last = self.ma.last_played() if self.ma else None
            if sock_title and state == "playing" and last and "/radio/" in last["uri"]:
                a["media_album_name"] = last["name"]
        try:
            a["volume_level"] = int(st.get("group volume", "").split()[0]) / 100
        except (ValueError, IndexError):
            pass
        if ss["muted"] is not None and rich:
            a["is_volume_muted"] = ss["muted"]
        last = self.ma.last_played() if self.ma else None
        if last and not a.get("media_title"):
            a["resume_name"] = last["name"]
        if cover is None and last and "/radio/" in last["uri"]:
            # Station logo as cover until the real artwork arrives (or when the queue is empty).
            if not a.get("media_title") or a.get("media_album_name") == last["name"]:
                cover = self.ma.last_logo_for()
        data = {"state": state, "attributes": a}
        if self.optimistic and time.time() < self.optimistic_until:
            if "state" in self.optimistic:
                data["state"] = self.optimistic["state"]
            a.update(self.optimistic.get("attributes", {}))
        error = None
        if "connected" not in (st.get("server") or ""):
            error = tr("err_no_ma")
        if ss["pin"]:
            error = tr("err_pairing", pin=ss["pin"])
        self._maybe_switch(state, sock_title, ss)
        return data, error, cover

    def _maybe_switch(self, state, sock_title, ss):
        """Send the display to the player's group if it shows something else."""
        now = time.time()
        if not ss["connected"] or state != "playing":
            self.mismatch_since = None
            return
        mismatch = ss["group_state"] != "playing" or not same_track(ss["meta"], sock_title)
        if not mismatch:
            self.mismatch_since = None
            return
        self.mismatch_since = self.mismatch_since or now
        if now - self.mismatch_since >= self.SWITCH_AFTER and now - self.last_switch >= self.SWITCH_AFTER:
            log.info("display is not showing what the player plays (%r vs %r): switch",
                     ss["meta"].get("title"), sock_title)
            self.last_switch = now
            self.ss.switch()

    def show_toast(self, text, secs=2.0):
        self.toast, self.toast_until = text, time.time() + secs

    def action(self, btn, data, value=None):
        a = (data or {}).get("attributes", {})
        if btn == "vol_set":
            # Pick a volume directly on the bar; also unmutes.
            self.optimistic = {"attributes": {"volume_level": value / 100, "is_volume_muted": False}}
            self.optimistic_until = time.time() + 3
            try:
                if not self.demo:
                    for line in ([f"vol {value}"] + (["mute off"] if a.get("is_volume_muted") else [])):
                        ok, text = self.sock.cmd(line)
                        if not ok:
                            raise RuntimeError(text)
                    self.sock.wake.set()
            except Exception as ex:
                self.optimistic = {}
                self.show_toast(tr("failed"))
                log.warning("setting volume failed: %s", ex)
            return
        self.pressed, self.pressed_until = btn, time.time() + 0.25
        playing = (data or {}).get("state") == "playing"
        last = self.ma.last_played() if self.ma and not self.demo else None
        if btn == "play" and not playing and last and not a.get("media_title"):
            # Nothing in the queue: restart the last played item right away.
            self.resume(last)
            return
        try:
            if btn == "play":
                line = "pause" if playing else "play"
                self.optimistic = {"state": "paused" if playing else "playing"}
            elif btn in ("prev", "next"):
                line = btn
                self.optimistic = {}
            elif btn in ("vol-", "vol+"):
                cur = round((a.get("volume_level") or 0) * 100)
                vol = min(100, max(0, cur + self.cfg["volume_step"] * (1 if btn == "vol+" else -1)))
                line = f"vol {vol}"
                self.optimistic = {"attributes": {"volume_level": vol / 100}}
            elif btn == "mute":
                muted = bool(a.get("is_volume_muted"))
                line = "mute off" if muted else "mute on"
                self.optimistic = {"attributes": {"is_volume_muted": not muted}}
            else:
                return
            self.optimistic_until = time.time() + 3
            if not self.demo:
                ok, text = self.sock.cmd(line)
                if not ok:
                    raise RuntimeError(text)
                self.sock.wake.set()
                if line == "play" and last:
                    threading.Thread(target=self._resume_if_silent, args=(last,), daemon=True).start()
        except Exception as ex:
            self.optimistic = {}
            if btn == "play" and not playing and last:
                self.resume(last)
                return
            self.show_toast(tr("failed"))
            log.warning("action %s failed: %s", btn, ex)

    def resume(self, last):
        """Restart the last played station/track through MA (the queue is empty or not playing)."""
        self.optimistic = {"state": "playing"}
        self.optimistic_until = time.time() + 5

        def done(ok):
            if not ok:
                self.optimistic = {}
            self.show_toast(last["name"] if ok else tr("resume_failed"), 2.5)
            self.sock.wake.set()
            self.dirty.set()

        self.ma.play_async(last, done, self.retrying)

    def _resume_if_silent(self, last):
        """'play' on the socket does nothing if MA lost the queue: then start the last played item after all."""
        time.sleep(4)
        st = self.sock.snapshot() or {}
        if (st.get("state") or "").split()[:1] != ["playing"]:
            log.info("play did nothing, resuming %s", last["name"])
            self.resume(last)

    def frame(self):
        data, err, cover = self.snapshot()
        if not self.cfg["show_cover"]:
            cover = None  # plain background, placeholder instead of the cover
        now = time.time()
        if (data or {}).get("state") == "playing":
            self.last_active = now
            # Remember what is playing: on every new track, and now and then (same title, other station).
            title = data["attributes"].get("media_title")
            if self.ma and (title != self.remembered[0] or now - self.remembered[1] > 120):
                self.remembered = (title, now)
                self.ma.remember_async()
        if self.toast and now > self.toast_until:
            self.toast = None
        if self.pressed is not None and now > self.pressed_until:
            self.pressed = None
        if self.power_busy:
            return (self.r.message(tr("power_busy_" + self.power_busy), [tr("power_wait_" + self.power_busy)]),
                    "power_busy", data)
        if now < self.settings_until:
            st = self.sock.snapshot() if not self.demo else None
            info = system_info(bool(st and "connected" in (st.get("server") or "")))
            if self.set_picker:
                return self.r.language_picker(_lang, self.pressed if isinstance(self.pressed, int) else None), \
                    "settings", data
            return self.r.settings_screen(self.cfg, self.set_tab, self.pressed, info, self.toast), "settings", data
        idle_long = now - self.last_active > self.cfg["clock_after_min"] * 60
        self.idle_anim = None
        if idle_long and now > self.awake_until and not self.demo:
            night = bool(self.cfg["night_after_min"]) and now - self.last_active > self.cfg["night_after_min"] * 60
            if self.cfg["idle_style"] == "digital" and not night:
                return self.r.clock(data, err, self.toast), "clock", data
            last = self.ma.last_played() if self.ma else None
            logo = self.ma.last_logo_for() if self.ma else None
            img = self.idle.render(datetime.datetime.now(), last, logo, night)
            if err or self.toast:
                img = img.copy()  # the cached picture must stay clean
                self.r._overlay(ImageDraw.Draw(img), err, self.toast)
            if self.idle.animated:
                self.idle_anim = self.idle
            return img, "clock", data
        if self.ma and now < self.stations_until:
            snap = self.ma.snapshot()
            if self.st_tab is None:
                self.st_tab = "fav" if snap["stations"]["fav"] else "all"
            if self.st_tab == "rooms":
                self.ma.refresh_rooms_async()  # keep the list current while it is on screen
            a = (data or {}).get("attributes", {})
            # For radio the station name is in the album field (or the title when there is no track).
            current = a.get("media_album_name") or a.get("media_title")
            pressed = self.pressed if isinstance(self.pressed, int) else None
            img, self.st_page = self.r.stations(snap, self.st_tab, self.st_page, current, pressed, self.toast,
                                                cover=cover)
            if self.room_confirm and now < self.room_confirm_until:
                c = self.room_confirm
                return (self.r.room_confirm(img, c["name"], c["add"], self.pressed if isinstance(self.pressed, str)
                                            else None), "room_confirm", data)
            self.room_confirm = None
            return img, "stations", data
        self.room_confirm = None
        img = self.r.player(data, cover, err, self.pressed, self.toast, show_stations=self.ma is not None,
                            show_viz=self.cfg["show_analyser"], show_cover=self.cfg["show_cover"])
        if now < self.power_until:
            return self.r.power_menu(img, self.pressed), "power", data
        if now < self.popup_until:
            return self.r.volume_popup(img, data, self.pressed), "volume", data
        return img, "player", data

    def stations_tap(self, x, y):
        now = time.time()
        self.stations_until = now + STATIONS_SECS
        hit = stations_hit(x, y)
        if not hit:
            return
        rooms = self.st_tab == "rooms"
        items = self.ma.snapshot()["rooms"]["rows"] if rooms else self.ma.snapshot()["stations"].get(self.st_tab) or []
        per = ROOM_ROWS if rooms else ST_COLS * ST_ROWS
        if hit[0] == "close":
            self.stations_until = 0
        elif hit[0] == "tab":
            self.st_tab, self.st_page = hit[1], 0
            if hit[1] == "rooms":
                self.ma.refresh_rooms_async(force=True)
        elif hit[0] == "left":
            self.st_page = max(0, self.st_page - 1)
        elif hit[0] == "right":
            self.st_page = min(max(0, (len(items) - 1) // per), self.st_page + 1)
        elif hit[0] == "tile" and rooms:
            self.rooms_tap(x, y, items, per)
        elif hit[0] == "tile":
            idx = self.st_page * per + hit[1]
            if idx >= len(items):
                if not items and self.ma.snapshot()["error"]:
                    self.ma.refresh_async(force=True)  # the list failed to load: a tap retries
                return
            station = items[idx]
            self.pressed, self.pressed_until = idx, now + 0.4

            def done(ok):
                self.show_toast(station["name"] if ok else tr("play_failed"), 2.5)
                if ok:
                    self.stations_until = 0
                    self.sock.wake.set()
                self.dirty.set()

            self.ma.play_async(station, done, self.retrying)

    def rooms_tap(self, x, y, items, per):
        """A tap in the rooms list: ask to add/remove that player (an offline one cannot be added)."""
        row_in_page = rooms_row_at(x, y)
        idx = None if row_in_page is None else self.st_page * per + row_in_page
        if idx is None or idx >= len(items):
            if not items and self.ma.snapshot()["rooms_error"]:
                self.ma.refresh_rooms_async(force=True)  # the list failed to load: a tap retries
            return
        row = items[idx]
        if not row["available"]:
            return
        now = time.time()
        self.pressed, self.pressed_until = idx, now + 0.4
        self.room_confirm = {"id": row["id"], "name": row["name"], "add": not row["member"]}
        self.room_confirm_until = now + 15

    def change_member(self, c):
        def done(ok):
            self.show_toast(tr("room_done_add" if c["add"] else "room_done_remove", name=c["name"]) if ok
                            else tr("room_failed"), 2.5)
            self.dirty.set()

        self.ma.set_member_async(c["id"], c["add"], done, self.retrying)

    def retrying(self):
        self.show_toast(tr("retrying"), 2.5)
        self.dirty.set()

    def handle_hold(self, x, y, screen):
        """A long press: on the clock of the player screen it opens the power menu."""
        now = time.time()
        self.awake_until = now + 30
        zx0, zy0, zx1, zy1 = POWER_ZONE
        if screen == "player" and zx0 <= x < zx1 and zy0 <= y < zy1:
            self.power_until = now + POWER_SECS

    def run_power(self, kind):
        """Run the configured shutdown/restart command; on failure the player screen comes back."""
        cmd = self.cfg[kind + "_command"]
        if self.demo:
            log.info("demo: would run %s", cmd)
            self.show_toast(tr("power_failed"), 3)
            return
        self.power_busy = kind

        def run():
            try:
                subprocess.run(cmd, check=True, timeout=30, capture_output=True)
                log.info("%s: %s", kind, " ".join(cmd))
            except Exception as ex:
                log.warning("%s failed: %s", kind, getattr(ex, "stderr", None) or ex)
                self.power_busy = None
                self.show_toast(tr("power_failed"), 3)
            self.dirty.set()
        threading.Thread(target=run, daemon=True).start()

    def settings_tap(self, x, y):
        now = time.time()
        self.settings_until = now + SETTINGS_SECS
        if self.set_picker:
            pick = picker_hit(x, y)
            if pick == "close":
                self.set_picker = False
            elif pick is not None:
                self.pressed, self.pressed_until = pick, now + 0.25
                self.apply_setting("language", LANGUAGE_CODES[pick])
                self.set_picker = False
            return
        hit = settings_hit(x, y, len(settings_rows(self.set_tab)))
        if hit is None:
            return
        if hit[0] == "close":
            self.settings_until = 0
            self.set_picker = False
        elif hit[0] == "tab":
            self.set_tab = hit[1]
        elif self.set_tab in ("general", "display"):
            sd = settings_rows(self.set_tab)[hit[1]]
            self.pressed, self.pressed_until = hit[1], now + 0.25
            if sd["kind"] == "action":
                self.reset_settings()
                return
            cur = self.cfg[sd["key"]]
            if sd["kind"] == "toggle":
                self.apply_setting(sd["key"], not cur)
                return
            if sd["key"] == "language":
                self.set_picker = True  # too many languages to cycle through: open the list
                return
            if sd["kind"] == "cycle":
                values = sd["values"]
                new = values[(values.index(cur) + 1) % len(values)] if cur in values else values[0]
            elif hit[2] == "row":
                return
            else:
                step = sd["step"] * (-1 if hit[2] == "minus" else 1)
                new = min(sd["max"], max(sd["min"], round((cur + step) / sd["step"]) * sd["step"]))
            self.apply_setting(sd["key"], new)

    def _sync_viz(self):
        """Tell the Sendspin client whether the analyser is wanted (it then drops the spectrum frames)."""
        if not self.demo:
            self.ss.viz_enabled = self.cfg["show_analyser"]

    def apply_setting(self, key, value):
        """Change a setting now and remember it across restarts."""
        self.cfg[key] = self.settings[key] = value
        if key == "language":
            set_language(value)
        self._sync_viz()
        if not save_settings(self.cfg["state_dir"], self.settings):
            self.show_toast(tr("settings_failed"), 3)

    def reset_settings(self):
        self.settings = {}
        for key in SETTING_KEYS:
            self.cfg[key] = self.base_cfg[key]
        set_language(self.cfg["language"])
        self._sync_viz()
        self.show_toast(tr("settings_reset_done") if save_settings(self.cfg["state_dir"], {})
                        else tr("settings_failed"), 2.5)

    def handle_tap(self, x, y, screen, data):
        now = time.time()
        self.awake_until = now + 30
        if screen == "power_busy":
            return
        if screen == "clock":
            # the "last station" tile resumes it; any other tap just wakes the player screen
            box, last = self.idle.tile_box, (self.ma.last_played() if self.ma else None)
            if box and last and box[0] <= x < box[2] and box[1] <= y < box[3]:
                self.resume(last)
            return
        if screen == "settings":
            self.settings_tap(x, y)
            return
        if screen == "power":
            btn = power_button_at(x, y)
            px0, py0, px1, py1 = POWER_PANEL
            if btn is None:
                # beside the panel: close; on the panel but between buttons: keep it open
                self.power_until = now + POWER_SECS if (px0 <= x < px1 and py0 <= y < py1) else 0
                return
            self.pressed, self.pressed_until = btn, now + 0.25
            self.power_until = 0
            if btn == "settings":
                self.settings_until, self.set_tab, self.set_picker = now + SETTINGS_SECS, "general", False
            elif btn != "cancel":
                self.run_power(btn)
            return
        if screen == "room_confirm":
            self.stations_until = now + STATIONS_SECS
            btn = confirm_button_at(x, y)
            px0, py0, px1, py1 = ROOM_CONFIRM
            if btn is None:
                if not (px0 <= x < px1 and py0 <= y < py1):
                    self.room_confirm = None  # tapped beside the panel: cancel
                return
            self.pressed, self.pressed_until = btn, now + 0.25
            c, self.room_confirm = self.room_confirm, None
            if btn == "yes" and c:
                self.change_member(c)
            return
        if screen == "stations":
            self.stations_tap(x, y)
            return
        if screen == "volume":
            x0, y0, x1, y1 = POPUP
            if not (x0 <= x < x1 and y0 <= y < y1):
                self.popup_until = 0  # tapped beside the panel: close
                return
            self.popup_until = now + POPUP_SECS
            value = popup_bar_value(x, y)
            if value is not None:
                self.action("vol_set", data, value)
                return
            btn = popup_button_at(x, y)
            if btn:
                self.action(btn, data)
            return
        btn = button_at(x, y)
        if btn == "volume":
            self.pressed, self.pressed_until = btn, now + 0.25
            self.popup_until = now + POPUP_SECS
        elif btn == "stations":
            if self.ma:
                self.stations_until = now + STATIONS_SECS
                self.st_tab, self.st_page = None, 0
                self.ma.refresh_async()
        elif btn:
            self.action(btn, data)

    def _start_touch(self):
        """Create the touch reader, retrying: with the early start the driver may not be up yet."""
        for attempt in range(120):
            try:
                Touch(self.cfg["touch_name"], self.cfg["calibration"], self.taps).start()
                return
            except Exception as e:
                if attempt == 0:
                    log.info("waiting for the touch device: %s", e)
                time.sleep(1)
        log.warning("touch not available")

    def _start_sendspin(self):
        """Start the Sendspin client once the network is up: it announces itself over mDNS, and an
        announcement made before there is an address would not be repeated."""
        if not wait_for_route():
            log.warning("no network after 120 s, starting the Sendspin client anyway")
        if self.ma:
            self.ma.refresh_async()  # the first attempt, at startup, usually ran before the network was up
        self.ss.start()

    def run(self, once=False):
        if not self.demo:
            self.sock.start()
            threading.Thread(target=self._start_sendspin, daemon=True).start()
            if self.ma:
                self.ma.refresh_async()  # fetch stations and logos up front
            time.sleep(0.5)  # wait for the first status
        if not once:
            threading.Thread(target=self._start_touch, daemon=True).start()
        last = None
        while True:
            img, screen, data = self.frame()
            on_player = screen == "player"
            marquees = self.r.marquees if on_player else []
            viz_box = self.r.viz_box if on_player else None
            idle = self.idle_anim if screen == "clock" else None
            if once:
                for strip, pos in self.animated_strips(img, marquees, viz_box, idle):
                    img.paste(strip, pos)
                self.sink.show(img)
                return
            b = img.tobytes()
            if b != last:
                # Put the moving parts (scrolling text, spectrum) into the full frame already;
                # otherwise they sit empty until the next strip update (flicker).
                shown = img.copy() if (marquees or viz_box or idle) else img
                for strip, pos in self.animated_strips(img, marquees, viz_box, idle):
                    shown.paste(strip, pos)
                self.sink.show(shown)
                if last is None:
                    log.info("first frame drawn")
                last = b
            # Wait for a tap, a state change or the next second; animate in the meantime.
            deadline = time.time() + (0.25 if (self.pressed is not None or self.toast) else 1.0)
            step = (1 / VIZ_FPS if viz_box else 1 / MARQUEE_FPS if marquees
                    else 1 / max(1, self.cfg["idle_fps"]) if idle else 0.1)
            while time.time() < deadline:
                try:
                    tap = self.taps.get(timeout=max(0.01, min(step, deadline - time.time())))
                    if len(tap) == 3:
                        self.handle_hold(tap[0], tap[1], screen)
                    else:
                        self.handle_tap(tap[0], tap[1], screen, data)
                    break
                except queue.Empty:
                    pass
                for strip, (x, y) in self.animated_strips(img, marquees, viz_box, idle):
                    self.sink.show_region(strip, x, y)
                if self.dirty.is_set():
                    self.dirty.clear()
                    break

    def animated_strips(self, base, marquees, viz_box, idle=None):
        """(strip, position) for everything that moves: scrolling lines, the spectrum and the idle clock's hands."""
        now = time.time()
        texts = {m.text for m in marquees}
        if texts != set(self.marquee_t0):
            # New text starts with the pause at the front again.
            self.marquee_t0 = {t: self.marquee_t0.get(t, now) for t in texts}
        strips = [(m.render(base, now - self.marquee_t0[m.text]), m.box[:2]) for m in marquees]
        if viz_box:
            self.spectrum.update(self.current_spectrum())
            strips.append((self.spectrum.render(base, viz_box, self.r.accent), viz_box[:2]))
        if idle:
            strips.append((idle.hands(datetime.datetime.now()), idle.face_box[:2]))
        return strips

    def current_spectrum(self):
        if self.demo:
            import math
            t = time.time()
            return [int(30000 * (0.55 + 0.45 * math.sin(t * 3 + i * 0.5)) * (1 - i / 40)) for i in range(VIZ_BINS)]
        return self.ss.current_spectrum()


def calibrate(cfg, sink):
    taps = queue.Queue()
    Touch(cfg["touch_name"], cfg["calibration"], taps, raw_mode=True).start()
    r = Renderer()
    targets = [(40, 40), (440, 40), (40, 280)]
    raws = []
    for i, (tx, ty) in enumerate(targets):
        img = r.message(tr("calibrate_title"), [tr("calibrate_step", n=i + 1)])
        d = ImageDraw.Draw(img)
        d.line((tx - 16, ty, tx + 16, ty), fill=ACCENT, width=3)
        d.line((tx, ty - 16, tx, ty + 16), fill=ACCENT, width=3)
        d.ellipse((tx - 6, ty - 6, tx + 6, ty + 6), outline=FG, width=2)
        sink.show(img)
        while not taps.empty():
            taps.get()
        raws.append(taps.get())
        time.sleep(0.6)
        while not taps.empty():
            taps.get()
    (x0, y0), (x1, y1), (x2, y2) = raws
    swap = abs(y1 - y0) > abs(x1 - x0)  # which raw axis changes on horizontal movement?
    h0, h1 = (y0, y1) if swap else (x0, x1)
    v0, v2 = (x0, x2) if swap else (y0, y2)
    sx = (h1 - h0) / 400
    sy = (v2 - v0) / 240
    cal = {"swap_xy": swap,
           "x_min": round(h0 - sx * 40), "x_max": round(h0 + sx * 440),
           "y_min": round(v0 - sy * 40), "y_max": round(v0 + sy * 280)}
    save_calibration(cal)
    print("calibration saved:", cal, "raw:", raws)
    sink.show(r.message(tr("calibrate_done"), [json.dumps(cal)]))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true")
    p.add_argument("--png")
    p.add_argument("--calibrate", action="store_true")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("aiosendspin").setLevel(logging.WARNING)
    cfg = load_config()
    set_language(cfg["language"])
    sink = PngSink(args.png) if args.png else open_framebuffer(cfg)
    try:
        if args.calibrate:
            calibrate(cfg, sink)
            return
        App(cfg, sink, demo=args.demo).run(once=bool(args.png))
    finally:
        sink.close()


if __name__ == "__main__":
    main()
