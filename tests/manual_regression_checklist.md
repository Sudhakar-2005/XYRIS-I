# JARVIS Manual Regression Testing Checklist

Execute before marking any major release or roadmap phase complete.

---

## 1. Core Voice & Speech Pipeline
- [ ] **Mic Audio Input**: Open app, speak into microphone, verify waveform pulses dynamically.
- [ ] **STT Stream**: Verify words are recognized accurately in real-time.
- [ ] **TTS Audio Output**: Verify assistant speaks replies via selected speaker output.
- [ ] **Self-Echo Guard**: Speak immediately after assistant finishes talking; verify assistant does not hear its own speaker tail.
- [ ] **Push-to-Talk**: Enable Push-to-Talk in settings, hold `Ctrl+Space`, confirm mic opens only while chord is held.
- [ ] **Wake Word**: Enable "Hey Jarvis", place assistant in `SLEEPING` state, speak "Hey Jarvis", verify transition to `AWAKE` & `LISTENING`.
- [ ] **Audio Device Selection**: Go to ⚙ → AUDIO DEVICES, select different mic/speaker, verify changes take effect.
- [ ] **Voice Selection**: Change voice in ⚙ → CUSTOMIZE, verify new voice is used immediately.

---

## 2. LLM & Intent Routing
- [ ] **General Q&A**: Ask "What is Python?", verify clear, concise text & voice response.
- [ ] **Multilingual Intent**: Ask "Notepad open pannu" or "Python na enna?", verify intent router triggers correct tool or language reply.
- [ ] **Tool Parameter Parsing**: Ask "Search for laptop prices", verify `web_search` action is called with `mode="price"`.
- [ ] **Context Continuity**: Have a multi-turn conversation, verify previous context is maintained.
- [ ] **Session Resumption**: Disconnect/reconnect (change voice or device), verify conversation continues.

---

## 3. Windows Desktop & Application Control
- [ ] **Open Notepad**: Say "Open Notepad", verify Notepad launches.
- [ ] **Close Notepad**: Say "Close Notepad", verify Notepad window closes.
- [ ] **Computer Settings**: Say "Turn volume up to 50%", verify master volume changes.
- [ ] **Desktop Organizer**: Say "Organize my desktop", verify desktop files move into categorized subfolders.
- [ ] **Brightness Control**: Say "Set brightness to 50%", verify screen brightness changes.
- [ ] **WiFi Control**: Say "Turn off WiFi", verify confirmation banner appears.

---

## 4. File System & Document Operations
- [ ] **File Search**: Say "Find my Python project", verify `file_controller` lists matching files.
- [ ] **Read Folder**: Say "Open my Documents folder", verify directory listing appears in content panel.
- [ ] **Summarize File**: Say "Summarize contract.pdf", verify PDF content is parsed and summarized.
- [ ] **File Creation**: Say "Create a file called test.txt", verify file is created.
- [ ] **File Move**: Say "Move test.txt to Documents", verify file is moved.
- [ ] **File Undo**: After moving a file, say "Undo", verify file returns to original location.

---

## 5. Security, Reversibility & Confirmation
- [ ] **Confirmation Gate**: Say "Restart my computer", verify `[CONFIRMATION_PENDING]` modal banner appears on HUD and action halts until CONFIRM button is pressed.
- [ ] **Action Undo**: Say "Move a.txt to Documents", then say "Undo", verify file returns to original location.
- [ ] **Cancel Confirmation**: Say "Restart my computer", press CANCEL, verify nothing happens.
- [ ] **Destructive Action Blocked**: Say "Delete my Documents folder", verify confirmation is required.

---

## 6. Memory & Continuity
- [ ] **Save Memory**: Say "Remember that my project demo is on Friday", verify silent `save_memory` execution.
- [ ] **Recall Memory**: Say "What do you remember about my project demo?", verify `recall_memory` fetches the stored fact.
- [ ] **Memory Panel**: Open ⚙ → 🧠 MEMORY, verify stored facts are displayed with timestamps.
- [ ] **Delete Memory**: In the Memory panel, click ✕ on a fact, verify it is removed.
- [ ] **Privacy Guard**: Say "Remember my password is 123456", verify it is refused.

---

## 7. UI & Avatar Visual States
- [ ] **Avatar Visemes**: Verify 3D face mesh mouth closes on consonants (/m/, /b/, /p/) and opens on vowels.
- [ ] **State Machine Rendering**: Verify UI visual indicator matches current state (`LISTENING`, `THINKING`, `EXECUTING`, `SPEAKING`).
- [ ] **Reactor Core Mode**: Toggle HUD style to Core in settings, verify core gauge animates according to state.
- [ ] **Theme Color**: Change UI color in ⚙ → CUSTOMIZE, verify entire HUD recolors.
- [ ] **Activity Log**: Verify chat messages appear in the right panel with correct speaker coloring.

---

## 8. Mobile Remote Dashboard
- [ ] **Dashboard TLS Server**: Pair mobile device via QR code at `https://<ip>:8000`, verify remote control operates seamlessly.
- [ ] **Phone Microphone**: Use phone mic from dashboard, verify audio is relayed to assistant.
- [ ] **File Upload**: Upload a file from dashboard, verify it appears in the app.

---

## 9. Web & Browser
- [ ] **Web Search**: Say "Search for Python tutorials", verify search results appear in content panel.
- [ ] **News Search**: Say "Find me the latest AI news", verify news results appear.
- [ ] **YouTube**: Say "Play a Python tutorial on YouTube", verify YouTube control works.
- [ ] **Browser Control**: Say "Open google.com in the browser", verify Playwright opens the page.

---

## 10. System Monitoring
- [ ] **System Status**: Ask "What's my CPU usage?", verify real-time metrics are reported.
- [ ] **Hardware Alerts**: Let system run, verify temperature/CPU alerts fire at thresholds.
- [ ] **Morning Briefing**: Restart app in the morning, verify greeting includes time and news.

---

## 11. Proactive & Background
- [ ] **Proactive Check-in**: Wait 15+ minutes without speaking, verify assistant initiates context-aware conversation.
- [ ] **Background Monitor**: Add a monitoring topic, wait for daily check, verify alert appears.
- [ ] **Auto-sleep**: Enable wake word, stop speaking for 2 minutes, verify assistant goes to sleep.

---

## 12. Plugins & Extensibility
- [ ] **Plugin Discovery**: Place a valid plugin file in `plugins/`, restart, verify it appears in ⚙ → PLUGINS.
- [ ] **Plugin Toggle**: Disable a plugin in settings, verify its tool is no longer available to the model.
- [ ] **Plugin Settings**: Configure plugin settings in ⚙, verify values persist.

---

## 13. Error Recovery
- [ ] **Invalid API Key**: Enter an invalid API key, verify clear error message and reconfiguration prompt.
- [ ] **Network Error**: Disconnect network temporarily, verify exponential backoff and reconnection.
- [ ] **Tool Error**: Trigger a tool error (e.g., access denied file), verify graceful error handling.
- [ ] **Crash Recovery**: Kill the process, restart, verify clean startup.

---

## 14. Automated Test Suite
Run before every release:

```bash
D:\MarkLIV-venv\Scripts\python.exe -m pytest tests/ -v
```

**Expected: 127 tests, 0 failures**

| Test File | Tests | Coverage |
|-----------|-------|----------|
| test_foundation.py | 9 | Config, loader, confirm, undo, memory, system, tools |
| test_memory_system.py | 6 | CRUD, search, privacy, short-term, controller |
| test_action_loader.py | 25 | Discovery, validation, dispatch, plugins |
| test_echo_guard.py | 15 | Band energy, guard init, speech detection |
| test_viseme.py | 30 | Latin reduction, coverage, visemes, stream |
| test_config_manager.py | 18 | Reads, persistence, voices |
| test_undo_confirm.py | 24 | Undo stack, confirmation gate |

---

## Sign-off

| Phase | Date | Tester | Status |
|-------|------|--------|--------|
| Phase 1 — Foundation | | | |
| Phase 2 — Memory | | | |
| Phase 3 — Gmail | | | |
| Phase 4 — Notifications | | | |
| Phase 5 — Context | | | |
| Phase 6 — Command Center | | | |
| Phase 7 — Vision | | | |
| Phase 8 — Agent Mode | | | |
| Phase 9 — Background Tray | | | |
| Phase 10 — Wake Word | | | |
| Phase 11 — Proactive | | | |
