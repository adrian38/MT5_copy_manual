# MT5 Copy Manual

Sistema local de observacion para copiar operaciones entre terminales MetaTrader 5.

Estado actual: solo observa, exporta CSVs, lee CSVs y detecta cambios. No abre,
modifica ni cierra operaciones.

## Estructura

- `mql5/Experts/SlaveObserver.mq5`: Expert Advisor observador para MT5.
- `mql5/Experts/DestinationObserver.mq5`: Expert Advisor observador para el
  terminal destino/copia.
- `config/settings.json`: configuracion central del lector Python.
- `src/mt5_copy/csv_reader.py`: lectura robusta de CSV/TSV desde Common Files.
- `src/mt5_copy/detector.py`: deteccion de nuevas posiciones, cambios SL/TP,
  cierres, nuevas ordenes pendientes, modificaciones y eliminaciones.
- `src/mt5_copy/event_log.py`: registro JSONL de eventos detectados.
- `src/mt5_copy/mapping.py`: archivo de mapeo `source_ticket` ->
  `destination_ticket`.
- `src/mt5_copy/executor.py`: capa preparada para PyAutoGUI, ahora en dry-run.
- `src/mt5_copy/app.py`: punto de entrada principal.

## Archivos exportados por el EA

El EA escribe en la carpeta Common Files de MetaTrader:

- `slave_positions.csv`
- `slave_orders.csv`
- `slave_heartbeat.csv`

El EA destino escribe:

- `destination_positions.csv`
- `destination_orders.csv`
- `destination_heartbeat.csv`

Por defecto usa tabulador como separador para evitar problemas con decimales y
comentarios.

## Uso

1. Copia `mql5/Experts/SlaveObserver.mq5` a `MQL5/Experts` del terminal MT5
   observador, compila y adjuntalo a un grafico.
2. Copia `mql5/Experts/DestinationObserver.mq5` a `MQL5/Experts` del terminal
   MT5 destino, compila y adjuntalo a un grafico.
3. Ajusta `common_files_path` en `config/settings.json` si tu ruta de Common
   Files es distinta.
4. Ejecuta un escaneo:

```powershell
python basic_reader.py
```

Tambien puedes ejecutar el modulo directamente:

```powershell
python -m src.mt5_copy.app --once
python -m src.mt5_copy.app
```

Interfaz local:

```powershell
python -m src.mt5_copy.app --ui
```

La interfaz permite iniciar, pausar y detener el seguimiento sin cerrar la
ventana. Tambien muestra si los heartbeats del terminal master y del terminal
destino estan activos, junto con cuenta, servidor, posiciones, ordenes y logs
del proceso. En la seccion `Ruta de copia` puedes elegir o escribir el terminal
origen, el terminal destino, sus prefijos CSV y el ID de ventana del MetaTrader
destino. Ese ID queda guardado tambien en `executor.mt5_window_title_contains`.

Para varios MetaTrader, crea un perfil por terminal en `config/settings.json`.
Cada perfil usa un `file_prefix`; el EA de ese terminal debe tener el mismo
`FilePrefix` y un `TerminalId` reconocible. Por ejemplo:

- terminal master: `TerminalId=master_1`, `FilePrefix=slave`
- terminal copia: `TerminalId=copy_1`, `FilePrefix=destination`

## Telegram

La interfaz puede reenviar el panel `Actividad de cambios` a Telegram. Configura
`notifications.telegram` en `config/settings.json`:

```json
"notifications": {
  "telegram": {
    "enabled": true,
    "bot_token": "...",
    "chat_id": "...",
    "prefix": "MT5 COPY"
  }
}
```

Se envia el resumen inicial y despues solo los logs filtrados de cambios,
warnings y errores.

## Control GUI MT5

La capa de control esta preparada pero bloqueada para trading real por defecto.
Sirve ya para localizar el terminal destino, enfocarlo y capturar pantalla:

```powershell
py -m src.mt5_copy.app --check-gui --list-windows
py -m src.mt5_copy.app --check-gui --screenshot
```

La ventana destino se configura en `config/settings.json`:

```json
"mt5_window_title_contains": "61350119"
```

Para activar la integracion por eventos, primero cambia:

```json
"mode": "pyautogui",
"pyautogui_enabled": true,
"armed_for_trading": false
```

Con `armed_for_trading=false`, el sistema puede enfocar MT5 y guardar
screenshots cuando detecta eventos, pero bloquea clicks, teclas y escritura.
No cambies `armed_for_trading` a `true` hasta haber validado el flujo completo
en una cuenta demo.

## Salidas locales

- `logs/app.log`: actividad del lector y eventos dry-run.
- `logs/events.log`: eventos detectados en formato JSONL.
- `data/state/last_snapshot.json`: ultimo snapshot usado para comparar.
- `data/mappings/ticket_mapping.csv`: mapeo preparado para tickets destino.
- `data/screenshots/`: capturas de pantalla de validacion GUI.
- `data/images/`: referencias visuales futuras para reconocimiento de imagenes.

## Seguridad

La ejecucion real esta desactivada:

- `executor.mode` esta en `dry_run`.
- `executor.armed_for_trading` esta en `false`.
- El EA no usa funciones de trading.

La siguiente fase puede implementar `PyAutoGuiExecutor` para hotkeys,
reconocimiento de imagenes y captura del ticket destino.
