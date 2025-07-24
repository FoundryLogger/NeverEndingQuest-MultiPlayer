# NeverEndingQuest - Guida Multiplayer

## 🎮 Trasformazione da Single-Player a Multiplayer

NeverEndingQuest è stato trasformato da un'applicazione single-player a un server multiplayer che supporta fino a 4 giocatori simultanei. Questa guida ti spiegherà come configurare e utilizzare il nuovo sistema multiplayer.

## 🚀 Installazione e Configurazione

### Prerequisiti

1. **Python 3.8+** installato sul sistema
2. **OpenAI API Key** configurata in `config.py`
3. **File di gioco esistenti** (party_tracker.json, moduli, ecc.)

### Installazione delle Dipendenze

```bash
pip install Flask Flask-SocketIO
```

### Configurazione

1. **Copia il template di configurazione:**
   ```bash
   cp config_template.py config.py
   ```

2. **Aggiungi la tua OpenAI API Key in config.py:**
   ```python
   OPENAI_API_KEY = "your_actual_openai_api_key_here"
   ```

3. **Verifica che i file di gioco esistano:**
   - `party_tracker.json`
   - `modules/conversation_history/conversation_history.json`
   - File dei moduli (es. `modules/The_Thornwood_Watch/`)

## 🎯 Avvio del Server Multiplayer

### Metodo 1: Avvio Diretto

```bash
python server.py
```

### Metodo 2: Avvio con Configurazione Personalizzata

```bash
# Modifica le impostazioni nel file server.py se necessario
# MAX_PLAYERS = 4  # Numero massimo di giocatori
# TURN_TIMEOUT = 300  # Timeout del turno in secondi
python server.py
```

### Output del Server

```
============================================================
NeverEndingQuest Multiplayer Server
============================================================
Maximum players: 4
Turn timeout: 300 seconds
============================================================
SUCCESS: Game state initialized
SUCCESS: Server ready for connections
============================================================
```

## 🌐 Connessione dei Giocatori

### Accesso al Server

1. **Apri il browser** su `http://localhost:5000`
2. **Inserisci il nome del tuo personaggio** nel form di join
3. **Clicca "Join Game"** per entrare nella partita

### Connessione di Rete Locale

Per permettere ai tuoi amici di connettersi dalla tua rete locale:

1. **Trova il tuo IP locale:**
   ```bash
   # Windows
   ipconfig
   
   # Linux/Mac
   ifconfig
   ```

2. **Condividi l'indirizzo:** `http://TUO_IP:5000`

3. **I tuoi amici possono connettersi** usando questo indirizzo

## 🎲 Funzionalità Multiplayer

### Sistema di Turni

- **Turno sequenziale:** I giocatori agiscono uno alla volta
- **Indicatore di turno:** L'interfaccia mostra chi sta giocando
- **Timeout automatico:** Se un giocatore non agisce entro 5 minuti, il turno passa automaticamente

### Chat Integrata

- **Chat in tempo reale** tra tutti i giocatori
- **Messaggi di sistema** per aggiornamenti di gioco
- **Separazione** tra azioni di gioco e chat

### Sincronizzazione dello Stato

- **Stato condiviso:** Tutti i giocatori vedono lo stesso stato di gioco
- **Aggiornamenti in tempo reale:** Le azioni di un giocatore sono visibili a tutti
- **Cronologia condivisa:** La conversazione è sincronizzata tra tutti i client

## 🎮 Interfaccia Multiplayer

### Pannello Principale

- **Output di Gioco:** Mostra le narrazioni del DM e le azioni dei giocatori
- **Input di Azione:** Campo per inserire le azioni del personaggio
- **Indicatore di Turno:** Mostra se è il tuo turno

### Pannello Giocatori e Chat

- **Informazioni di Gioco:** Turno corrente, numero di giocatori, tempo di gioco
- **Lista Giocatori:** Mostra tutti i giocatori connessi
- **Chat:** Messaggi in tempo reale tra i giocatori

### Stati dell'Interfaccia

- **"It's your turn!"** - È il tuo turno, puoi agire
- **"Waiting for [Player]..."** - Aspetti che un altro giocatore agisca
- **"Not Your Turn"** - Il pulsante è disabilitato

## 🔧 Configurazione Avanzata

### Modifica del Numero Massimo di Giocatori

Nel file `server.py`, modifica la variabile `MAX_PLAYERS`:

```python
MAX_PLAYERS = 6  # Aumenta a 6 giocatori
```

### Modifica del Timeout del Turno

```python
TURN_TIMEOUT = 600  # 10 minuti per turno
```

### Personalizzazione del Server

```python
# Modifica la porta del server
socketio.run(app, host='0.0.0.0', port=8080, debug=False, allow_unsafe_werkzeug=True)

# Modifica l'indirizzo di ascolto
socketio.run(app, host='192.168.1.100', port=5000, debug=False, allow_unsafe_werkzeug=True)
```

## 🛠️ Risoluzione Problemi

### Problema: "Game state not initialized"

**Soluzione:**
1. Verifica che `party_tracker.json` esista
2. Controlla che i moduli siano presenti in `modules/`
3. Assicurati che `config.py` sia configurato correttamente

### Problema: "Connection refused"

**Soluzione:**
1. Verifica che il server sia in esecuzione
2. Controlla che la porta 5000 sia libera
3. Verifica il firewall di Windows

### Problema: "OpenAI API key not found"

**Soluzione:**
1. Copia `config_template.py` in `config.py`
2. Aggiungi la tua OpenAI API key
3. Riavvia il server

### Problema: I giocatori non vedono gli aggiornamenti

**Soluzione:**
1. Verifica la connessione WebSocket
2. Controlla la console del browser per errori
3. Riconnetti i client

## 📋 Comandi Utili

### Avvio del Server
```bash
python server.py
```

### Verifica dello Stato del Gioco
```bash
# Controlla che i file esistano
ls party_tracker.json
ls modules/conversation_history/conversation_history.json
```

### Reset del Server
```bash
# Ferma il server (Ctrl+C)
# Riavvia
python server.py
```

## 🎯 Best Practices

### Per il Dungeon Master (Host)

1. **Avvia il server** prima di invitare i giocatori
2. **Verifica la connessione** con un browser locale
3. **Condividi l'IP** con i giocatori
4. **Monitora la console** per errori o problemi

### Per i Giocatori

1. **Usa nomi unici** per i personaggi
2. **Aspetta il tuo turno** prima di agire
3. **Usa la chat** per comunicare fuori dal gioco
4. **Riconnetti** se perdi la connessione

### Gestione della Sessione

1. **Salva regolarmente** lo stato del gioco
2. **Comunica** le pause o interruzioni
3. **Coordina** le azioni di gruppo
4. **Usa la chat** per strategie

## 🔄 Migrazione da Single-Player

### Preservazione del Progresso

Il server multiplayer utilizza gli stessi file del gioco single-player:

- `party_tracker.json` - Stato del party
- `modules/conversation_history/conversation_history.json` - Cronologia
- File dei personaggi in `modules/[MODULE]/characters/`
- File dei moduli in `modules/[MODULE]/`

### Compatibilità

- **Stesso sistema di combattimento**
- **Stesso sistema di leveling**
- **Stessi moduli e avventure**
- **Stessa logica di gioco**

## 🎉 Funzionalità Speciali

### Chat in Tempo Reale
- Comunicazione istantanea tra giocatori
- Separazione tra azioni di gioco e chat
- Storico dei messaggi

### Indicatori di Stato
- Connessione del server
- Turno corrente
- Giocatori connessi
- Stato del gioco

### Sincronizzazione Automatica
- Aggiornamenti in tempo reale
- Stato condiviso tra tutti i client
- Gestione automatica dei turni

## 📞 Supporto

Se incontri problemi:

1. **Controlla la console del server** per errori
2. **Verifica la configurazione** di `config.py`
3. **Controlla i file di gioco** per integrità
4. **Riavvia il server** se necessario

---

**Buon divertimento con NeverEndingQuest Multiplayer!** 🎲⚔️🏰 