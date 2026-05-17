# Yahli: Autonomous AI Chief of Staff

Most AI assistants answer questions. Yahli executes.

[![Google Hackathon Submission](https://img.shields.io/badge/Google-Hackathon_Submission-blue.svg)](#)
[![Powered by Gemma 4](https://img.shields.io/badge/Model-Gemma_4-orange.svg)](#)
[![Privacy First](https://img.shields.io/badge/Privacy-Local-green.svg)](#)

> **Video Pitch:** [Insert YouTube/Drive Link Here]
> **Live Demo:** [Insert Link if applicable]
> **Core Capabilities:** [Explore FEATURES.md](FEATURES.md)

## The Problem & Our Story

People with ADHD are overwhelmed by the invisible administrative burden of daily life. Tasks, reminders, deadlines, messages, and important information are scattered across WhatsApp chats, emails, notes, PDFs, and calendars. Existing productivity tools are often too complex, rigid, or demanding to maintain consistently. As a result, critical tasks are forgotten, routines collapse, stress accumulates, and both personal and professional life are negatively affected.

**Yahli is an Autonomous Chief of Staff.** Built natively to run locally (Privacy-First), it transforms natural language into full-scale database management, data visualization, and autonomous task orchestration. It doesn't just answer questions; it manages projects, nags suppliers via WhatsApp, balances workloads, and tracks team well-being.

---

## Hackathon Tracks Alignment

We designed Yahli with specific real-world impact goals, targeting the following tracks:

### 1. Health & Sciences: Data to Actionable Visuals

- **The Gap:** Medical/NGO staff cannot code or build complex dashboards.
- **Our Solution:** Using the `visualize_data` and PDF OCR features, users can say: _"Show me a graph of mental health requests by city over the last month based on the PDF reports."_ Yahli automatically extracts the data, writes a Pandas/Matplotlib Python script in the background, and returns a clean, understandable graph. We bridge the gap between raw medical/field data and human decision-making.

### 2. Global Resilience: Autonomous Crisis Management

- **The Gap:** In a crisis, managers forget to follow up.
- **Our Solution:** The **TIC (Task Information Center)**. Yahli initiates long-term "Missions." If an NGO manager needs to coordinate a medical supply delivery, Yahli will proactively send a WhatsApp to the supplier. If the supplier doesn't reply, Yahli autonomously reminds them or alerts the manager. It's a resilient system that anticipates failures and manages follow-ups without human intervention.

### 3. Digital Equity & Inclusivity: No-Code Flow Builder

- **The Gap:** Creating automations (Zapier/Make) requires technical skills, excluding grassroots organizations from the AI revolution.
- **Our Solution:** The AI Architect. A user simply types: _"When a new volunteer registers, match their skills to open roles and send them a WhatsApp."_ Yahli dynamically builds a complete JSON-based execution flow with loops, conditions, and webhooks. High-end automation made accessible to anyone.

### 4. Ollama: Local Execution for Maximum Privacy

- **The Gap:** Medical records and crisis coordination data cannot be sent to cloud APIs due to HIPAA/GDPR and unreliable internet in disaster zones.
- **Our Solution:** Yahli's core reasoning engine can run completely offline using **Gemma 4 via Ollama**. Total data sovereignty, ensuring sensitive field data never leaves the local machine.

---

## Key Features

- **Human-Centric Time Logic:** The system operates on a "6 AM day reset" logic. If you add a task at 1:00 AM for "tomorrow," Yahli knows you mean the upcoming daylight hours, not 24 hours later.
- **Semantic Duplicate Detection:** Before creating a task, Yahli checks the database semantically. "Call Shmuel" and "Phone Shmuel" will trigger a duplicate warning, preventing database pollution.
- **Auto-Advancing Project Management:** Projects are divided into weekly phases. When the final task of a phase is marked complete, Yahli automatically rolls the project to the next phase and generates the new relevant tasks.
- **Smart WhatsApp & Communication Engine:** Yahli matches natural language to exact DB records (e.g., "Text my brother" -> semantically maps to the correct contact ID). It drafts context-aware messages and sends them via WhatsApp Web automation (to individuals or groups).
- **Workload-Aware Auto-Scheduling:** Tell Yahli your work hours (e.g., "Sunday 9 to 1"). Yahli blocks the Google Calendar and intelligently distributes pending tasks into that block based on urgency, semantic grouping, and deadlines.
- **Dynamic Memory Compression:** To prevent context-window overflow during long autonomous missions, the TIC automatically summarizes chat history after 7 interactions, retaining only actionable data while clearing raw text.
- **Yahli's Room (EQ & Well-being):** A dedicated space for the user. It tracks mood, captures "transparent wins" (things you did that weren't on your task list), and adjusts the tone of the Daily Morning Briefing and Evening Report accordingly.

---

## Under the Hood (Technical Architecture)

- **Backend Engine:** Flask (Python) handling highly asynchronous workflows.
- **Autonomous Agent Logic:** Custom ReAct (Reasoning and Acting) loop with explicit fail-safes against hallucinations.
- **Database:** Lightweight, JSON-based localized Knowledge Base, making it highly portable and easy to back up.
- **Task Information Center (TIC):** A state-machine memory system for agents. It holds variables like `mission_state`, `next_actionable_task`, and `scheduling_preferences`, allowing long-term operations without losing context.
- **Data Ingestion:** Seamlessly creates new database schemas on the fly from raw text or Google Sheets imports.

## Getting Started

### Required API Keys & Credentials

Before you can run Yahli, you need to obtain several API keys and credentials. Here's exactly how to get each one. If you get stuck at any step, you can ask ChatGPT for a walkthrough — just paste the step name and it'll guide you through it.

---

#### 1. Google Gemini API Key

**What it's for:** Powers Yahli's AI brain — all reasoning, text generation, and file analysis.

**How to get it:**

1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Sign in with your Google account
3. Click **"Create API Key"**
4. Select or create a Google Cloud project
5. Copy the generated API key (starts with `AIza...`)
6. Paste it into your `.env` file as `GOOGLE_GEMINI_API_KEY`

**Free tier:** Gemini offers a generous free tier with rate limits. For heavy usage, you may want a paid plan.

---

#### 2. Google Custom Search API Key + Search Engine ID

**What it's for:** Enables Yahli to search the web for real-time information, research, and fact-checking.

This requires **two separate things** — an API key and a Search Engine ID. You need both.

**Step A — Get the API Key:**

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select an existing one)
3. Go to **APIs & Services > Library**
4. Search for **"Custom Search API"** and click **Enable**
5. Go to **APIs & Services > Credentials**
6. Click **"Create Credentials" > "API Key"**
7. Copy the generated key — this is your `GOOGLE_API_KEY`

**Step B — Get the Search Engine ID:**

1. Go to [Google Programmable Search Engine](https://programmablesearchengine.google.com/)
2. Click **"Add"** to create a new search engine
3. Give it a name (e.g., "Yahli Search")
4. Under **"What to search"**, select **"Search the entire web"**
5. Click **Create**
6. On the next page, find your **Search Engine ID** (a string like `a1b2c3d4e5f6g7h8i`)
7. Copy it — this is your `SEARCH_ENGINE_ID`

**Free tier:** 100 search queries per day for free.

---

#### 3. Telegram Bot Token + Admin Chat ID

**What it's for:** Yahli communicates with you through Telegram — sends notifications, receives tasks, and manages missions.

**Step A — Get the Bot Token:**

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a name for your bot (e.g., "My Yahli Assistant")
4. Choose a username (must end in `bot`, e.g., `my_yahli_bot`)
5. BotFather will reply with your **API token** (looks like `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)
6. Copy it — this is your `TELEGRAM_BOT_TOKEN`

**Step B — Get Your Admin Chat ID:**

1. Open Telegram and search for **@userinfobot** (or **@RawDataBot**)
2. Send it any message (e.g., `/start`)
3. It will reply with your user info — look for the **Id** field (a number like `5079958108`)
4. Copy that number — this is your `ADMIN_CHAT_ID`

**Free:** Completely free.

---

#### 4. Google OAuth Credentials (Calendar, Tasks, Gmail, Sheets)

**What it's for:** Lets Yahli read/write your Google Calendar, manage Tasks, check Gmail, and access Google Sheets.

**How to get them:**

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Select your project (or create a new one)
3. Go to **APIs & Services > Library** and enable these APIs:
   - **Google Calendar API**
   - **Google Tasks API**
   - **Gmail API**
   - **Google Sheets API**
4. Go to **APIs & Services > OAuth consent screen**
   - this is WEB APPLICATION
   - Fill in the required fields (app name, user support email, developer email)
   - Choose **"External"** user type
   - add in the Authorized redirect URIs http://localhost:8080 and http://localhost:8080/
   - inside data access Add scopes:
     https://www.googleapis.com/auth/calendar
     https://www.googleapis.com/auth/tasks
     https://www.googleapis.com/auth/gmail.readonly
     https://www.googleapis.com/auth/spreadsheets.readonly
   - Add your own email as a test user _this is important step you must add your user as test_
   - Save and continue
5. Go to **Clients**
6. Create and download the JSON file and rename it to **`credentials.json`**
7. Place `credentials.json` in the project root folder

wait some time so everything will be defined
if it does not ask gemini.

**First run:** When you start Yahli for the first time, it will open a browser window asking you to authorize access to your Google account. After you approve, it will automatically create a `token.json` file. You only need to do this once.

---

#### 5. WhatsApp Desktop Integration

**What it's for:** Yahli sends autonomous WhatsApp messages to individuals and groups — follows up with suppliers, sends reminders, and manages crisis coordination without you lifting a finger.

**How to set it up:**

**Step A — Install WhatsApp Desktop:**

1. Download and install **WhatsApp Desktop** from the [Microsoft Store](https://apps.microsoft.com/detail/9nbdxk72vk2m) (Windows only)
2. Open WhatsApp Desktop and link it to your phone by scanning the QR code
3. Keep WhatsApp Desktop **logged in and running** when Yahli is active

**Step B — Create the `chat_ready.png` screenshot:**

Yahli uses image recognition to know when a WhatsApp chat has fully loaded before sending a message. You need to provide a reference screenshot:

1. Open WhatsApp Desktop and open any chat
2. Take a screenshot of the **message input area** (the text box where you type messages)
3. Crop it to a small region (~100x50 pixels) showing just the input field placeholder text
4. Save it as **`chat_ready.png`** in the **project root folder** (same directory as `main.py`)

> **Tip:** Open any chat, zoom in slightly, and capture the area that says "Type a message" or "הקלד הודעה". This helps Yahli wait until the chat UI is fully ready before pasting and sending.

**Step C — Add contacts and groups to your database:**

Yahli manages recipients through its JSON-based Knowledge Base. You need to add contacts and groups:

- **For individuals:** Add entries with `type: "contact"` and a `phone_number` field (international format, e.g., `+1234567890`)
- **For groups:** Add entries with `type: "group"` and a `group_id` field (the invite code from the group link)

Example via Yahli's natural language interface:

```
"Add my brother as a contact, phone number +972501234567"
"Add the Crisis Response Team as a group with invite code abc123"
```

**Important notes:**

- **Windows only:** The WhatsApp automation uses Windows-specific desktop automation libraries
- **Keep WhatsApp Desktop running:** Yahli needs the app to be open and logged in
- **Don't use your computer while Yahli sends:** The automation takes control of keyboard/mouse briefly
- **Hebrew/multilingual support:** Messages are pasted via clipboard, so all languages are fully supported

**Dependencies (already in requirements.txt):**

- `pyautogui` — screen recognition and keyboard control
- `pygetwindow` — window management
- `pyperclip` — clipboard operations for multilingual text
- `keyboard` — reliable paste commands that bypass language issues

---

### Prerequisites Checklist

Before installing, make sure you have:

- [ ] Python 3.10+ installed
- [ ] Google Gemini API key
- [ ] Google Custom Search API key + Search Engine ID
- [ ] Telegram Bot Token + your Telegram Chat ID
- [ ] Google OAuth `credentials.json` file (for Calendar/Tasks/Gmail/Sheets)
- [ ] WhatsApp Desktop installed and logged in (Windows only)
- [ ] `chat_ready.png` screenshot in project root (for WhatsApp automation)

### Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/YourUsername/Yahli-AI.git
   cd Yahli-AI
   ```

2. Create and activate a virtual environment:

   ```bash
   python -m venv venv
   source venv/bin/activate   # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Set up environment variables:

   ```bash
   cp .env.example .env
   ```

   Open `.env` in any text editor and fill in all the values you collected above. Each variable is clearly labeled.

5. Place your Google OAuth credentials in the project root:
   - `credentials.json` (downloaded from Google Cloud Console)
   - `token.json` (auto-generated after first OAuth authorization)

6. Start Yahli:

   ```bash
   python main.py
   ```

7. Open in your browser:
   ```
   http://localhost:5000
   ```

### Environment Variables Reference

All sensitive configuration is managed through the `.env` file. Here's what each variable does:

| Variable                        | Description                                                                | Required |
| ------------------------------- | -------------------------------------------------------------------------- | -------- |
| `GOOGLE_GEMINI_API_KEY`         | Gemini API key for AI reasoning (paid key required for better performance) | Yes      |
| `GOOGLE_API_KEY`                | Google Custom Search API key (for web research)                            | Yes      |
| `SEARCH_ENGINE_ID`              | Google Programmable Search Engine ID                                       | Yes      |
| `TELEGRAM_BOT_TOKEN`            | Telegram bot token from @BotFather                                         | Yes      |
| `ADMIN_CHAT_ID`                 | Your Telegram user ID (from @userinfobot)                                  | Yes      |
| `GEMINI_MODEL_NAME`             | Primary model for general tasks                                            | No       |
| `GEMINI_MODEL_FOR_COMPLEX_NAME` | Model for complex reasoning tasks                                          | No       |
| `GEMINI_VISION_MODEL`           | Model for vision/OCR tasks                                                 | No       |
| `EMBEDDING_MODEL`               | Model for vector memory embeddings                                         | No       |

### WhatsApp Integration Notes

WhatsApp automation does **not** require API keys. Instead, it uses desktop automation:

| Requirement      | Description                                          | Status              |
| ---------------- | ---------------------------------------------------- | ------------------- |
| WhatsApp Desktop | Must be installed from Microsoft Store and logged in | Required            |
| `chat_ready.png` | Screenshot of chat input area for UI recognition     | Required            |
| Windows OS       | Desktop automation libraries are Windows-specific    | Required            |
| `pyautogui`      | Screen recognition and keyboard control              | In requirements.txt |
| `pyperclip`      | Clipboard operations for multilingual text           | In requirements.txt |
| `keyboard`       | Reliable paste commands bypassing language issues    | In requirements.txt |
| `pygetwindow`    | Window management and focus control                  | In requirements.txt |

**Troubleshooting:**

- **"WhatsApp did not open in time"**: Ensure WhatsApp Desktop is installed (not WhatsApp Web) and can be opened via the `whatsapp://` protocol
- **"Chat interface failed to load"**: Recreate `chat_ready.png` with a clearer screenshot of the input area
- **Messages not sending**: Keep WhatsApp Desktop in the foreground and avoid using your computer during message dispatch
- **Hebrew text appears garbled**: The system uses clipboard-based paste (`Ctrl+V`) which handles all languages correctly — ensure your keyboard layout is not interfering

---

### Customizing Your Personal Profile

Yahli includes your personal information in WhatsApp message prompts so the AI writes messages in your voice. Before using, update your details in `app/prompts/system_prompts.py`:

- **`handle_communication_request_promt_personal`**: Update Gender, Interests, Creative Background, and Intellectual Interests
- **`handle_communication_request_promt_group`**: Same fields for group messages

These sections control how Yahli drafts messages on your behalf, so personalize them to match your personality and background.

---

Built with love for the Google AI Hackathon 2026.
"# personalAssistant" 
"# personalAssistant" 
