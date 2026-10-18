# BUGS.md — esiti della review read-only (2026-07-03)

Report di una sessione di review indipendente sul codebase. Nessun fix è stato applicato:
questo file è la lista di lavoro per le prossime sessioni. I finding marcati
**[verificato]** sono stati riprodotti eseguendo codice reale (script in scratchpad,
output nella sessione di review); gli altri sono confermati per lettura del codice.

Ordine: prima i bug di correttezza, poi i rischi, poi i miglioramenti mirati.
Ogni voce: dove, cosa, perché, fix proposto.

---

## STATO FIX (aggiornato 2026-07-04)

Tutti gli 8 bug e i 9 rischi hanno un blocco **✅ RISOLTO** col fix e i nomi dei
test. Prove reali raccolte in questa sessione (output nella sessione, non solo
nomi di test — dopo che un test "verde ma vuoto" aveva mascherato il bug 5):

- **Suite completa:** `python -m pytest` → **215 passed, 0 failed, 0 skipped**
  (0 skip ⇒ i test gated su ffmpeg/LibreOffice/OCR/pdf2docx hanno GIRATO davvero).
- **Bug 1** riprodotto live via `run_job` json→yaml: output annidato
  (`user:`→`name:`/`roles:`), nessuna chiave `user.name`, roundtrip == originale.
- **Bug 2** riprodotto live: csv→**tsv**, csv→**xlsx** e csv→**json** preservano
  `01234`/`1.10`/`00500` (prima `1234`/`1.1`/`500`). Fix esteso a TUTTI i target
  (non solo csv↔tsv — vedi voce 2).
- **Bug 8** provato end-to-end con file REALE: pdf→odt via `run_job` `ok=True`,
  odt da 14 KB, testo presente (il 2° salto docx→odt di `run_convert` funziona
  dopo il fix del gating del successo).
- **Bug 9 (FROZEN):** exe ricostruito (`pyinstaller … --clean`, exit 0, niente
  `torch`); dall'exe `--convert` pdf→docx `ok=True` (36 KB) e pdf→odt `ok=True`
  (14 KB), testo presente; `--selftest` → **PASS 15/15**, exit 0. Il processo
  figlio `Converter.exe --pdf2docx` PARTE nel frozen (il docx prodotto lo prova:
  senza pdf2docx importabile il figlio darebbe `ok=False`).

**Limiti di copertura noti (onestà):** bug 8 — il ramo "output parziale dopo
kill" è testato SOLO con `run_with_timeout` mockato (un kill reale a metà
scrittura è non deterministico); il percorso di SUCCESSO è provato live. Bug 13 —
testato docx→docx, non pdf→pdf (stesso ramo `if src==dst: shutil.copy`). Bug 14 —
testati gif→gif e gif→png, non tiff-multipagina né gif→webp (stesso ramo
`save_all`). Bug 16 — testato l'helper `_gs_version_key`, non l'intero scan di
`find_ghostscript`.

---

## BUG DI CORRETTEZZA

### 0. `data.py` — xlsx→xlsx / ods→ods / xml→xml dichiarati LOSSLESS "Same format." ma il round-trip pandas perde dati **[verificato]** ✅ RISOLTO (2026-07-04)

> Finding di una review successiva (2026-07-04): stesso identico difetto del bug 13
> (`document.py` src==dst) ma per il converter dati, sfuggito alla prima passata.
> `verdict()` cortocircuita `src == dst` → `lossless("Same format.")` PRIMA dei check
> spreadsheet/xml, mentre `convert()` instrada xlsx/ods/xml sul round-trip pandas
> (solo valori, solo primo foglio). Verificato live via `run_job`: un xlsx con 2 fogli
> + una formula → output con 1 foglio, formula persa, badge LOSSLESS "Same format.".
> Raggiungibile: il combo target elenca anche le coppie stesso-formato.
>
> Fix (scelta utente: byte-copy, preserva i dati — come document.py): nuovo
> `DataConverter._same_format_copy` — per src==dst in {xlsx, ods, xml} `shutil.copy`
> fedele, così il lossless è ONESTO e fogli/formule/struttura sopravvivono. Se
> `strip_metadata` è attivo, `_strip_spreadsheet_metadata` rimuove le proprietà del
> documento dopo la copia (openpyxl azzera le properties; odfpy svuota `meta`),
> lasciando i dati intatti — coerente con le immagini (metadati preservati salvo
> richiesta esplicita). csv/tsv e json/yaml NON rientrano: hanno opzioni same-format
> significative (header, pretty/orient) e percorsi già fedeli.
> Regressioni: `test_same_format_xlsx_preserves_sheets_and_formulas`,
> `test_same_format_xml_is_byte_identical`,
> `test_same_format_xlsx_strips_metadata_only_on_request` (sostituisce il vecchio
> `test_document_metadata_does_not_leak`, che sanciva come feature-privacy il
> comportamento lossy). Suite: **217 passed, 0 skipped**. Verificato live post-fix:
> xlsx→xlsx `ok=True`, LOSSLESS, 2 fogli + formula preservati.

- **Dove:** `converter/converters/data.py:47-48` (verdetto), `convert()` r. 183+.
- **Cosa:** xlsx→xlsx tramite pandas legge solo i valori del primo foglio; formule,
  formattazione e fogli extra vengono scartati, ma il badge dice LOSSLESS "Same
  format." — la violazione esatta che l'app vieta.

### 1. `data.py` — json→yaml e json→json dichiarati LOSSLESS ma trasformano i dati **[verificato]** ✅ RISOLTO (2026-07-03)

> Fix: nuovo percorso `DataConverter._convert_tree` — json↔yaml (e json→json,
> yaml→yaml) NON passano più da pandas/json_normalize: `json.load`/`yaml.safe_load`
> → dump dell'altro, annidamento preservato. Regressioni:
> `test_json_to_yaml_preserves_nesting`, `test_json_to_json_preserves_object`,
> `test_yaml_to_json_preserves_nesting`. I target tabellari (json→csv/xlsx/...)
> restano sul percorso DataFrame con verdetto lossy corretto.
> Verificato live (2026-07-04) col payload esatto del report via `run_job`:
> output annidato, nessuna chiave `user.name`, roundtrip == originale.


- **Dove:** `converter/converters/data.py:56` e `:48` (verdetti), `_read()` r. 93-103.
- **Cosa:** ogni sorgente JSON passa da `pd.json_normalize` → DataFrame → records.
  Verificato: `{"user": {"name": "Ada", "roles": ["a","b"]}, "n": 1}` →
  - json→yaml: `- n: 1\n  user.name: Ada\n  user.roles:\n  - a\n  - b` — annidamento
    distrutto, chiavi appiattite con il punto, oggetto avvolto in una lista;
  - json→json ("Same format." LOSSLESS): l'oggetto diventa una **lista** di record
    appiattiti.
- **Perché è grave:** viola il principio fondante dell'app (mai mentire sulla qualità).
  Il verdetto dice "JSON and YAML share the same data model" ma il percorso di codice
  non preserva il modello.
- **Fix proposto:** per json↔yaml (e json→json) convertire albero→albero senza pandas:
  `json.load` → `yaml.safe_dump` (e viceversa con `yaml.safe_load` → `json.dump`).
  Il DataFrame serve solo per i target tabellari (csv/tsv/xlsx/ods/xml); per quei
  target da sorgente JSON il verdetto lossy "Nested structures are flattened" è già
  corretto. Declassare a lossy qualunque percorso che continui a passare da
  `json_normalize`.

### 2. `data.py` — csv→tsv dichiarato LOSSLESS ma pandas re-inferisce i tipi **[verificato]** ✅ RISOLTO (2026-07-03, esteso 2026-07-04)

> Fix (2026-07-04, definitivo): `_read` per sorgenti csv/tsv legge SEMPRE con
> `dtype=str, keep_default_na=False`, verso QUALUNQUE target — un CSV/TSV è testo
> senza tipi, quindi preservarlo come stringa è la lettura fedele. Copre csv→tsv,
> csv→**xlsx/ods** e csv→**json**, i cui verdetti "LOSSLESS / maps faithfully"
> ora sono onesti. (La prima versione 2026-07-03 limitava il dtype=str al solo
> csv↔tsv e lasciava csv→xlsx/json a mentire — gap trovato in verifica.)
> Regressioni: `test_csv_to_tsv_preserves_leading_zeros_and_decimals`,
> `test_csv_to_xlsx_preserves_values`, `test_csv_to_json_preserves_values`.
> Verificato live via `run_job`: `01234`/`1.10`/`00500` preservati su tsv, xlsx e json.


- **Dove:** `converter/converters/data.py:53-54` (verdetto), `_read()` r. 79-81.
- **Cosa:** `pd.read_csv` senza `dtype` inferisce i tipi. Verificato:
  `01234` → `1234`, `1.10` → `1.1`, `00500` → `500`. Il verdetto dice
  "Only the field delimiter changes; all data is preserved" — falso: zeri iniziali
  e precisione decimale persi.
- **Fix proposto:** per le coppie csv↔tsv leggere con `dtype=str` (e
  `keep_default_na=False`) così cambia davvero solo il delimitatore. Valutare lo
  stesso per csv/tsv→xlsx/ods ("Each row maps faithfully" — anche lì i valori passano
  dall'inferenza).

### 3. GUI — collisione dei path di output nel batch **[verificato]** ✅ RISOLTO (2026-07-03)

> Fix: `resolve_collision(path, reserved=None)` e `build_output_path(..., reserved=None)`
> ora considerano preso un nome se esiste su disco OPPURE è già nel set `reserved`.
> `_convert_all` tiene un `set` dei path assegnati nel batch e lo passa/aggiorna per
> ogni item → due sorgenti con lo stesso stem ottengono `photo.jpg` e `photo (1).jpg`.
> Regressioni: `tests/test_naming.py` (`test_resolve_collision_honors_reserved`,
> `test_batch_same_stem_gets_distinct_outputs`) + il test a livello GUI
> `test_batch_same_stem_gets_distinct_paths_via_convert_all` (2026-07-04), che
> guida davvero `_convert_all` con due file omonimi e prova che il cablaggio del
> set `reserved` c'è (senza, entrambi risolverebbero a `photo.bmp`).


- **Dove:** `converter/gui/main_window.py:596` (`_convert_all`) +
  `converter/core/naming.py:15` (`resolve_collision`).
- **Cosa:** `build_output_path` viene chiamato per TUTTI gli item del batch prima che
  qualunque conversione scriva. `resolve_collision` controlla `path.exists()` su file
  che ancora non esistono. Verificato: `C:\dirA\photo.png` e `C:\dirB\photo.png` verso
  la stessa cartella di output risolvono entrambi a `photo.jpg` → due worker scrivono
  in parallelo lo stesso file (sovrascrittura silenziosa/corruzione).
- **Perché è grave:** `naming.py` promette esplicitamente "non sovrascrivere mai
  silenziosamente".
- **Fix proposto:** tenere in `_convert_all` un set dei path già assegnati nel batch e
  passarlo a `resolve_collision` (collisione = esiste su disco OPPURE già assegnato),
  oppure riservare il nome creando subito il file vuoto con `open(..., "x")`.

### 4. GUI — Clear durante la conversione → IndexError e batch mai concluso ✅ RISOLTO (2026-07-03)

> Fix: (a) guardia `0 <= row < len(self.model.items)` in `_on_finished` — se la
> riga non esiste più non si tocca il modello, ma `_completed` viene comunque
> incrementato così `_finish_batch` parte e l'UI si sblocca; (b) `clear_btn` è ora
> un attributo di istanza, disabilitato in `_convert_all` e riabilitato in
> `_finish_batch` (Clear spento durante il batch). Regressioni:
> `tests/test_gui_batch.py` (`test_clear_during_batch_does_not_wedge`,
> `test_clear_disabled_while_converting`).


- **Dove:** `converter/gui/main_window.py:370` (`_clear` sempre abilitato) e
  `:622` (`_on_finished` fa `item = self.model.items[row]` SENZA la guardia che
  `_on_progress` ha a r. 615).
- **Cosa:** con un batch in corso, Clear svuota `model.items`; il primo segnale
  `finished` che arriva solleva IndexError nello slot Qt, `_completed` non incrementa
  mai, `_finish_batch` non viene chiamato e `convert_btn` resta disabilitato per
  sempre (UI bloccata).
- **Fix proposto:** (a) guardia `if not (0 <= row < len(self.model.items)): return`
  in `_on_finished` (contando comunque `_completed`); (b) disabilitare Clear (e ogni
  rimozione di righe) mentre `cancel_btn` è abilitato, cioè durante un batch.

### 5. `registry` — la UI offre una coppia che `run_job` poi rifiuta **[verificato]** ✅ RISOLTO (2026-07-03)

> Fix: `resolve` ora, tra i converter che `can_convert` (già ordinati per priorità
> decrescente), preferisce il primo pienamente disponibile (`_pair_fully_available`:
> `available().ok` + `format_available(src/dst)` + `pair_available`); se nessuno è
> disponibile ripiega sul più prioritario, così il messaggio d'errore di run_job
> resta sensato. Regressioni in `tests/test_registry_resolve.py` (motore alto-priorità
> mockato assente → resolve sceglie LibreOffice; entrambi presenti → vince il
> prioritario; entrambi assenti → fallback al prioritario).


- **Dove:** `converter/core/registry.py:81-84` (`targets_for` preferisce il converter
  *disponibile*) vs `:88-93` (`resolve` prende il primo per *priorità*, ignorando la
  disponibilità).
- **Cosa:** se il converter ad alta priorità è indisponibile e uno a bassa priorità
  copre la stessa coppia, `targets_for` mostra la coppia come disponibile ma `resolve`
  instrada comunque a quello indisponibile. Verificato: striprtf assente + LibreOffice
  presente in FULL → UI: rtf→txt `available=True`; `resolve("rtf","txt")` →
  `RtfTextConverter` → `run_job` fallisce con "RTF text extraction needs the
  'striprtf' package". Stesso schema per pdf→txt/html se manca pdfminer.
- **Fix proposto:** in `resolve`, tra i converter che `can_convert`, saltare quelli
  con `available().ok == False` se ne esiste un altro disponibile che copre la coppia
  (mantenendo il fallback all'indisponibile a più alta priorità se nessuno è
  disponibile, così il messaggio d'errore resta sensato). Aggiungere un test di
  regressione con un motore ad alta priorità mockato assente.
- **Nota test:** `tests/test_phase3_invariants.py:24` mocka *tutti* i motori presenti,
  quindi la suite non può catturare questo mismatch — è il buco esatto in cui vive il
  bug.

### 6. `detection.py` — AAC ADTS rilevato come MP3 (confidenza 0.99) **[verificato]** ✅ RISOLTO (2026-07-03)

> Fix: il check ADTS (`\xff\xf1` / `\xff\xf9`) è stato spostato PRIMA del check mp3
> in `_magic` (e il ramo aac morto più in basso rimosso). Quei pattern a 12 bit sono
> ADTS, non mp3; un sync mp3 reale (0xFFFB) continua a risultare mp3. Regressioni:
> `test_aac_adts_not_detected_as_mp3`, `test_real_mp3_frame_still_detected`.


- **Dove:** `converter/core/detection.py:77` (check mp3) vs `:89` (check aac, di fatto
  irraggiungibile).
- **Cosa:** il check mp3 usa `head[0] == 0xFF and (head[1] & 0xE0) == 0xE0` (3 bit),
  che intercetta anche il sync ADTS (0xFFF1/0xFFF9, 12 bit). Verificato: file ADTS →
  `DetectedFormat('mp3', confidence=0.99, method='magic')`. Il ramo `aac` a r. 89 è
  codice morto per quelle firme.
- **Fix proposto:** spostare il check ADTS (`\xff\xf1` / `\xff\xf9`) PRIMA del check
  mp3, oppure usare per mp3 la maschera corretta a 11 bit
  (`(head[1] & 0xE6) in (0xE2, 0xE4, 0xE6)` o simile — sync 0xFFE + layer ≠ 0).

### 7. `detection.py` — JSON pretty >4KB rilevato come YAML/CSV **[verificato]** ✅ RISOLTO (2026-07-03)

> Fix: in `detect()`, se lo head *strippato* inizia con `{` o `[` si tenta SEMPRE
> `_full_json(path)` prima di fidarsi dell'esito csv/tsv/yaml dello sniff (non più
> solo quando `fmt is None`); corretto anche `head[:1].lstrip()` → `head.lstrip()[:1]`
> (guardava un solo byte, saltava i file che iniziano con whitespace). Regressioni:
> `test_large_pretty_json_detected`, `test_json_with_leading_whitespace_detected`.


- **Dove:** `converter/core/detection.py:159-166` (sniff con head troncato) e `:217`
  (retry full-file).
- **Cosa:** con head di 4096 byte, il parse JSON del testo troncato fallisce e lo
  sniffing cade su CSV/YAML (virgole e `": "` abbondano in un JSON). Il retry
  `_full_json` scatta solo se `fmt is None`, e la condizione usa
  `head[:1].lstrip()` che guarda **un solo byte** (un file che inizia con
  whitespace/newline non rientra mai). Verificato: JSON pretty da 28KB →
  `DetectedFormat('yaml', 0.7, 'text-sniff')`.
- **Conseguenza:** routing e verdetti sbagliati; combinato col bug 1, anche il
  contenuto convertito cambia.
- **Fix proposto:** in `detect()`, se il testo dello head *strippato* inizia con `{` o
  `[`, tentare sempre `_full_json(path)` prima di accettare l'esito csv/tsv/yaml dello
  sniff; correggere `head[:1].lstrip()` in `head.lstrip()[:1]`.

### 8. `_libreoffice.run_convert` — successo con output parziale dopo timeout/kill ✅ RISOLTO (2026-07-03)

> Fix: il successo richiede ora `not timed_out and code == 0 and produced.exists()`
> (prima bastava `produced.exists()`, così un file troncato da un kill a metà
> scrittura passava per riuscito). Su timeout/crash l'eventuale output parziale
> viene rimosso (`produced.unlink()`) prima di ricostruire il profilo. Regressioni:
> `test_timeout_with_partial_output_is_not_success`,
> `test_crash_with_partial_output_is_not_success`, più la contro-prova
> `test_clean_success_returns_produced_file` (exit 0 + file → resta successo).
>
> **Copertura (onestà):** i test del ramo di FALLIMENTO usano `run_with_timeout`
> MOCKATO + un file parziale pre-creato (un kill reale a metà scrittura è non
> deterministico, non riproducibile in modo affidabile). Il percorso di SUCCESSO
> è invece provato END-TO-END con LibreOffice reale (2026-07-04): pdf→odt via
> `run_job` `ok=True`, odt da 14 KB col testo atteso — il 2° salto docx→odt che
> usa `run_convert` funziona ancora dopo il fix.


- **Dove:** `converter/converters/_libreoffice.py:114-117`.
- **Cosa:** dopo `run_with_timeout`, `produced.exists()` viene controllato PRIMA di
  considerare `timed_out`/`code`: se soffice viene killato a metà scrittura ma il file
  esiste (troncato), la funzione ritorna `(True, path)` e il file corrotto viene
  spacciato per conversione riuscita. Usato da `document.py` e `pdf_import.py`
  (secondo salto docx→odt/rtf).
- **Fix proposto:** considerare il risultato valido solo se `not timed_out and
  code == 0`; su timeout/crash eliminare l'eventuale file parziale in `out_dir` prima
  di tornare l'errore.

---

## RISCHI

### 9. `pdf_import` — pdf2docx e pdfminer in-process senza timeout né cancel ✅ RISOLTO (2026-07-03)

> Fix (perf, punti a+b): `_has_extractable_text` ora usa
> `extract_text(str(src), maxpages=_SCAN_CHECK_MAX_PAGES=5)` — il gate scansione legge
> solo le prime pagine invece di estrarre tutto il PDF due volte (validate + convert),
> che con maxpages resta comunque economico. Regressione: `test_scan_gate_reads_limited_pages`.
>
> Fix (punto c): pdf2docx gira ora in un PROCESSO FIGLIO killabile. `_pdf_to_docx`
> lancia `converter_app --pdf2docx <src> <dst>` (nuovo `_pdf2docx` hook che chiama
> `_pdf_to_docx_inprocess`) via `run_with_timeout` (timeout duro `_PDF2DOCX_TIMEOUT=300s`
> + kill dell'albero + env pulito) — un PDF ostile non gela più il worker; l'output
> parziale viene rimosso su timeout/errore. Fallback in-process se non si riesce a
> lanciare il figlio (nessuna perdita di funzione). Regressioni:
> `test_pdf_to_docx_subprocess_produces_docx`, `test_pdf_to_docx_subprocess_rejects_garbage`.
>
> **Nota residua:** l'estrazione pdfminer (`_ocr_pdf_to_docx`) resta in-process, ma gira
> DOPO l'OCR (già killabile) su un PDF ormai bonificato — rischio marginale, non wrappato.
>
> **VERIFICATO NEL FROZEN (2026-07-04):** exe ricostruito (`pyinstaller … --clean`,
> exit 0). Dall'exe: `Converter.exe --convert doc.pdf docx …` → `ok=True` (docx 36 KB,
> testo presente) e `… odt …` → `ok=True` (odt 14 KB, testo presente); `--selftest`
> → PASS 15/15, exit 0. Il figlio `Converter.exe --pdf2docx` PARTE nel frozen: il
> docx prodotto lo dimostra (senza pdf2docx importabile nel figlio, `ok=False`). In
> frozen `_pdf2docx_command` restituisce sempre il comando subprocess (nessun
> fallback in-process), quindi il successo prova proprio il ramo figlio.

- **Dove:** `converter/converters/pdf_import.py:102-119` (`_pdf_to_docx`) e `:58-67`
  (`_has_extractable_text`).
- **Cosa:** il modulo applica la disciplina "timeout duro + kill dell'albero" a
  OCRmyPDF e LibreOffice, ma il salto principale (`cv.convert()` di pdf2docx) e
  l'estrazione pdfminer girano in-process: un PDF ostile gela il worker per sempre e
  `job.cancel` è ignorato in tutto il converter (il docstring di `_ocr_pdf` ammette
  che l'in-process "non sarebbe interrompibile" — vale anche per pdf2docx).
- **In più (performance):** `_has_extractable_text` estrae TUTTO il testo del PDF
  (nessun `maxpages`) e viene chiamata due volte (in `validate()` e in `convert()`):
  su un PDF di centinaia di pagine sono due estrazioni complete solo per decidere
  "scansione o no".
- **Fix proposto:** (a) per il gate scansione usare
  `extract_text(str(src), maxpages=N)` con early-exit appena si superano
  `_MIN_TEXT_CHARS`; (b) memoizzare l'esito tra validate e convert (es. cache
  path+mtime) o accettare la doppia chiamata ma con maxpages; (c) valutare di eseguire
  pdf2docx in un processo figlio killabile (subprocess con lo stesso runner di
  `_libreoffice.run_with_timeout`) — più invasivo, decidere prima se ne vale la pena.

### 10. `_ffmpeg.py` — nessun timeout complessivo; cancel dipende dallo stdout ✅ RISOLTO (2026-07-03)

> Fix: (a) `probe_duration` ora passa `timeout=_PROBE_TIMEOUT=15` a `subprocess.run`
> (un ffprobe appeso viene killato e si degrada a durata sconosciuta, invece di gelare
> la coda). (b) `run_ffmpeg` ha un watchdog thread: uccide ffmpeg se arriva il cancel
> mentre la lettura di stdout è bloccata (cancel funziona anche senza progress) o se
> ffmpeg resta muto oltre `_STALL_TIMEOUT=120s` (processo impiccato). Uccidere chiude
> stdout → il ciclo riceve EOF → ritorno pulito (rc 130 "Cancelled." o messaggio di
> stallo). Regressioni: `test_probe_duration_passes_timeout`,
> `test_run_ffmpeg_cancel_stops_and_removes_partial` (ffmpeg reale), e il preesistente
> `test_run_ffmpeg_large_stderr_does_not_deadlock` conferma nessuna regressione sul
> percorso normale.

- **Dove:** `converter/converters/_ffmpeg.py:61-111` (`run_ffmpeg`), `:44-58`
  (`probe_duration`).
- **Cosa:** a differenza di LibreOffice/pandoc/OCR, ffmpeg non ha un timeout duro; il
  cancel è controllato solo quando arriva una riga su stdout (`-progress pipe:1`): se
  ffmpeg si blocca senza emettere progress, né cancel né timeout lo fermano → coda
  gelata. `probe_duration` è un `subprocess.run` SENZA timeout, chiamato in
  `validate()`/`estimate_size`: un ffprobe appeso gela la coda prima ancora di
  convertire.
- **Fix proposto:** `timeout=15` (o simile) su `probe_duration`; per `run_ffmpeg`
  aggiungere un watchdog (es. thread timer o `proc.stdout.readline` con deadline) che
  killa il processo dopo N secondi senza progresso, riusando l'idea di
  `run_with_timeout`.

### 11. Output parziali lasciati su disco dopo fallimento/cancel ✅ RISOLTO (2026-07-03)

> Fix: pulizia dell'output su fallimento/cancel con `unlink(missing_ok=True)`:
> `run_ffmpeg` rimuove `output_path` su cancel/stallo/exit≠0 (rischio 10 incluso);
> `markup.py` (pandoc) su timeout ed exit≠0; `pdf.py` (pdfminer) nel ramo except.
> Così non resta un file rotto accanto al messaggio d'errore. Regressioni:
> `test_pandoc_removes_partial_output_on_failure`, `test_pandoc_timeout_removes_partial_output`,
> `test_pdf_removes_partial_output_on_failure`, `test_run_ffmpeg_cancel_stops_and_removes_partial`.
> Nota: non ho adottato il pattern "tmp + os.replace" (più invasivo); l'unlink su
> fallimento copre il sintomo descritto (file rotto + nome buono già consumato).

- **Dove:** ffmpeg (audio.py/video.py: `-y` scrive direttamente su
  `job.output_path`); pandoc (`converter/converters/markup.py:103-104`: `-o` sulla
  destinazione finale — su timeout resta il file a metà); pdfminer html
  (`converter/converters/pdf.py:63`).
- **Cosa:** nessuno di questi converter pulisce l'output su errore/cancel: l'utente
  trova un file rotto accanto al messaggio di errore, e il nome "buono" è già stato
  consumato da `resolve_collision`.
- **Fix proposto:** pattern unico: scrivere su file temporaneo nella stessa cartella e
  `os.replace` a successo (come già fanno document.py/pdf_import.py con la
  TemporaryDirectory), oppure `unlink(missing_ok=True)` dell'output quando si ritorna
  `ok=False`/cancel.

### 12. `converter_app.py` — stdout senza guardia in `_selftest`/`_dumpenv`; `gen_media` senza env pulito ✅ RISOLTO (2026-07-03)

> Fix: guardia `if sys.stdout:` in `_selftest` e `_dumpenv` (nell'exe windowed
> `sys.stdout` è None → niente più AttributeError dopo aver scritto il report, quindi
> exit code coerente col PASS/FAIL); `gen_media` ora passa
> `env=clean_subprocess_env(), creationflags=CREATE_NO_WINDOW` come gli altri tool
> esterni; la tmpdir di `_selftest` viene rimossa con `shutil.rmtree` a fine run.
> Regressione: `test_dumpenv_survives_no_stdout` (stdout mockato None → rc 0, report scritto).

- **Dove:** `converter_app.py:232` e `:245` (`sys.stdout.write` senza la guardia
  `if sys.stdout:` che `_convert` ha a r. 279-280); `:38` (`gen_media`).
- **Cosa:** nell'exe windowed `sys.stdout` può essere None → `--selftest` scrive il
  report e POI muore con AttributeError → exit code 1 anche con report PASS
  (incoerenza report/exit-code). Inoltre `gen_media` lancia ffmpeg con
  `subprocess.run` senza `clean_subprocess_env()` né `CREATE_NO_WINDOW`, in contrasto
  con la regola documentata in `deps.clean_subprocess_env` per i tool esterni nel
  frozen exe. Infine `tempfile.mkdtemp()` (r. 33) non viene mai rimosso.
- **Fix proposto:** guardare `if sys.stdout:` in `_selftest` e `_dumpenv`; passare
  `env=clean_subprocess_env(), creationflags=CREATE_NO_WINDOW` a `gen_media`;
  avvolgere il tmpdir in `tempfile.TemporaryDirectory()` o pulirlo a fine selftest.

### 13. `document.py` — verdict `src==dst` lossless "Same format." ma la conversione è reale ✅ RISOLTO (2026-07-03)

> Fix: `DocumentConverter.convert` con `src == dst` fa ora una copia byte-a-byte
> (`shutil.copy`) invece del round-trip LibreOffice (che ri-importava il PDF come Draw
> o round-trippava il docx) — così il verdict lossless "Same format." è ONESTO.
> Regressione: `test_same_format_document_is_byte_copy`.

- **Dove:** `converter/converters/document.py:72-73` (il check `src == dst` viene
  PRIMA di `src == "pdf"`).
- **Cosa:** pdf→pdf passa dall'import Draw (ricostruzione approssimativa — la stessa
  che il modulo dichiara imperfetta per pdf→html) ed esce marcato LOSSLESS;
  docx→docx è un round-trip import/export LibreOffice, non una copia byte-a-byte.
- **Fix proposto:** o copiare davvero il file quando src==dst (shutil.copy → allora
  lossless è onesto), o dare il verdetto del percorso reale (pdf→pdf lossy "re-imported
  as Draw"; rich→rich lossy best-effort). Decidere prima quale comportamento si vuole.

### 14. `image.py` — sorgenti multi-frame perdono tutto tranne il primo frame, in silenzio ✅ RISOLTO (2026-07-03)

> Fix: `convert()` rileva `is_animated` (`n_frames > 1`) e verso i container che
> reggono più frame (gif/webp/tiff) passa `save_all=True`, preservando tutti i
> frame/pagine. Verso un formato a frame singolo (png/bmp/jpg/ico/heic/avif) il
> risultato NON è più marcato lossless: il verdict del ConversionResult diventa lossy
> "only the first is kept". Regressioni: `test_animated_gif_to_gif_preserves_frames`,
> `test_animated_gif_to_png_is_lossy_first_frame`.

- **Dove:** `converter/converters/image.py:132-178` (nessun `save_all`).
- **Cosa:** GIF animata → gif/webp/png = solo il primo frame; TIFF multipagina idem.
  Il verdict di gif parla solo di palette (r. 93-94), nessuna menzione della perdita
  dell'animazione.
- **Fix proposto:** minimo: dichiararlo nel verdict per sorgenti gif/tiff. Meglio:
  `save_all=True` per gif→gif, gif→webp, tiff→tiff (Pillow lo supporta), con
  `img.n_frames > 1` come discriminante.

### 15. `image.py` — EXIF Orientation né applicata né preservata verso png/bmp ✅ RISOLTO (2026-07-03)

> Fix: all'apertura, per le sorgenti a frame singolo, `img = ImageOps.exif_transpose(img)`
> applica l'orientamento ai PIXEL (una foto verticale non esce più coricata verso
> png/bmp) e rimuove il tag Orientation, quindi la copia dell'EXIF non lo riapplica.
> Gating su frame singolo per non collassare le animazioni (interazione col rischio 14).
> Regressione: `test_exif_orientation_applied_to_png` (jpg 20×10 Orientation=6 → png 10×20).

- **Dove:** `converter/converters/image.py:141-147` (l'EXIF si allega solo a
  jpg/webp/tiff/heic/avif).
- **Cosa:** jpg con Orientation=6 → png: l'EXIF si perde (png non è nella lista) e i
  pixel NON vengono trasposti → la foto scattata in verticale esce coricata, con
  verdict "lossless: stores exact pixels".
- **Fix proposto:** `from PIL import ImageOps; img = ImageOps.exif_transpose(img)`
  all'apertura (e rimuovere il tag Orientation dall'EXIF che si preserva, per non
  applicarlo due volte).

### 16. `deps.find_ghostscript` — sort lessicografico delle versioni **[verificato]** ✅ RISOLTO (2026-07-03)

> Fix: nuovo `_gs_version_key` (tuple di interi dai numeri nel nome, es. gs10.02 →
> (10, 2)) usato come `key` dell'ordinamento, così 10.x batte 9.x invece di perdere
> col confronto stringa. Regressione: `test_ghostscript_version_sort_is_numeric`.

- **Dove:** `converter/core/deps.py:138`.
- **Cosa:** `sorted(..., reverse=True)` su Path ordina come stringa:
  `gs9.56` > `gs10.02` → con due versioni installate vince la più vecchia.
  Impatto basso (il finder serve solo al gate informativo; OCRmyPDF trova gs dal
  registro), ma il fix è una riga.
- **Fix proposto:** ordinare con chiave numerica, es.
  `key=lambda d: tuple(int(x) for x in re.findall(r"\d+", d.name)) or (0,)`.

### 17. `pdf_import.available()` richiede LibreOffice anche per pdf→docx ✅ RISOLTO (2026-07-03)

> Fix: tolto il check `find_soffice()` da `available()` (che ora richiede solo
> modalità FULL + pdf2docx) e spostato in un nuovo `pair_available(src, dst)` per i
> soli dst odt/rtf (il secondo salto docx→odt/rtf via LibreOffice). Così pdf→docx
> resta disponibile anche senza LibreOffice; pdf→odt/rtf va in grigio col motivo
> giusto e run_job la rifiuta pulita. Regressioni:
> `test_full_without_soffice_docx_available_odt_rtf_greyed` (ex `test_full_without_soffice_unavailable`,
> aggiornato al nuovo comportamento) e `test_pdf_to_docx_resolves_without_soffice`.

- **Dove:** `converter/converters/pdf_import.py:178-180`.
- **Cosa:** pdf→docx usa solo pdf2docx, ma senza soffice l'intera direzione (docx
  incluso) è rifiutata con "LibreOffice was not found" — messaggio fuorviante e
  degrado non granulare, in contrasto con la filosofia di `pair_available` (il
  Full-only per modalità è un'altra cosa ed è documentato; il gate soffice su docx no).
- **Fix proposto:** togliere il check soffice da `available()` e metterlo in
  `pair_available(src, dst)` per i soli dst odt/rtf (secondo salto LibreOffice).

---

## VERIFICATO E A POSTO (non toccare per questi motivi)

- **mp3-con-cover→wav senza `-vn`** (`audio.py:163`): NON è un bug — testato con
  ffmpeg reale, il muxer wav scarta da solo lo stream video (exit 0).
- **Fix del deadlock stderr in `run_ffmpeg`** (stderr su file temporaneo): corretto;
  `tests/test_ffmpeg_runner.py` esercita davvero la condizione (verifica perfino che
  lo stderr del carico superi i 64KB).
- `run_job`/`run_batch`, `quality.py`, `options.py`, `history.py`, `presets.py`,
  `workers.py`, `queue_model.py`: nessun problema rilevante trovato.
- I test letti (pdf_import, phase3_invariants, ffmpeg_runner) sono onesti; l'unico
  buco strutturale è quello annotato al punto 5.

## NON COPERTO DALLA REVIEW

- `dialogs.py`, `options_panel.py`, `badge_delegate.py`, `util.py` e la parte alta di
  `main_window.py` — NON coperti dalla prima passata, ma **letti nella review di
  follow-up del 2026-07-04** (vedi la sezione in fondo): trovati e corretti 2 fix minori.
  `theme.py` resta letto solo in parte.

## PRIORITÀ SUGGERITA

1. Bug 1-2 (verdetti falsi sui dati — tradiscono direttamente l'utente).
2. Bug 3-4 (perdita dati / UI bloccata in GUI).
3. Bug 5-8 (routing incoerente, detection sbagliata, falso successo LibreOffice).
4. Rischi 9-17 in ordine di elenco.

Regole per chi fixa: ogni fix con test di regressione che riproduce il caso
verificato (gli script empirici usati in review: csv con zeri iniziali, JSON annidato,
JSON pretty >4KB, header ADTS `\xff\xf1`, due sorgenti con lo stesso stem, striprtf
mockato assente). Eseguire `python -m pytest -q` e controllare anche il conteggio
degli skip.

---

## REVIEW DI FOLLOW-UP (2026-07-04)

Seconda passata sui file non coperti dalla prima (`dialogs.py`, `options_panel.py`,
`badge_delegate.py`, `util.py`, parte alta di `main_window.py`) + ri-audit dei converter
dati/immagini. Esito: **1 bug di onestà** (già in cima come **bug 0**) + **2 fix minori**.
Suite dopo i fix: da 218 a 221, poi 236 col converter 3D. Ogni fix ha un test di regressione.

### 0. `data.py` xlsx→xlsx / ods→ods / xml→xml LOSSLESS falso ✅ RISOLTO
Vedi il blocco **### 0.** in cima (stessa classe del bug 13, sfuggito per i dati). Fix:
`DataConverter._same_format_copy` — byte-copy per src==dst di xlsx/ods/xml (scelta owner:
preservare i dati), strip dei metadati onorato. Prove live nella sessione: un xlsx con 2
fogli + formula usciva a 1 foglio senza formula con badge LOSSLESS.

### A. GUI — colonna "Result" della cronologia sempre verde ✅ RISOLTO
- **Dove:** `gui/dialogs.py` `HistoryDialog._reload` (colonna col 4).
- **Cosa:** una conversione RIUSCITA ma lossy veniva tinta di verde (leggeva come
  "lossless"). Il colore ignorava il verdetto reale.
- **Fix:** colore per livello reale — `ok`+lossy → ambra, `ok`+lossless → verde,
  `failed` → rosso (`badge_color("failed" if ... else (entry.verdict or "lossless"))`).

### B. GUI — `_add_paths` crashava se un file spariva prima dell'elaborazione ✅ RISOLTO
- **Dove:** `gui/main_window.py::_add_paths` (`path.stat().st_size`).
- **Cosa:** un file droppato/selezionato che spariva tra la selezione e l'elaborazione
  faceva sollevare `OSError` e abortiva l'aggiunta dell'intero lotto.
- **Fix:** `stat()`+`detect()` in `try/except OSError`: il file si salta, il resto entra,
  lo status riporta il conteggio saltati. Regressione:
  `test_add_paths_skips_vanished_files_without_crashing`.
