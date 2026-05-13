import os
import json
import requests
from flask import Flask, request, jsonify
import google.generativeai as genai

app = Flask(__name__)

# ─── CONFIG (set these in environment variables) ───────────────────────────────
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")
WHATSAPP_TOKEN    = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
VERIFY_TOKEN      = os.environ.get("VERIFY_TOKEN", "mfn_secret_2024")

# ─── SYSTEM PROMPT (MFN AI Agent Brain) ───────────────────────────────────────
SYSTEM_PROMPT = """
You are the Official Sales Expert for MFN (Baku, Azerbaijan).
Your goal is to qualify leads for Termo Aqlay facade systems via WhatsApp.

STRICT RULES:
- NEVER give a final price. If asked, say price depends on object type and wall condition.
- Speak like a professional engineer. Never say "As an AI" or "I'm happy to help".
- Use only the name "MFN". Never mention founding year or specific warranty years.
- Say "uzunmüddətli davamlılıq" (AZ) or "долговечность" (RU) instead of warranty years.
- Always respond in the language the user chose (AZ or RU).

KEY SELLING POINTS:
- No black rough plaster (qara suvaq) needed — saves 5-7 AZN per m2
- 10x lighter than stone
- Energy efficient — reduces heat loss
- Reference projects: Sea Breeze, Hyatt Regency, Sabah Residence (all in Baku)

PRICE RESPONSE (when asked):
AZ: "Dəqiq qiymət üçün əvvəlcə obyektin növünü və divar vəziyyətini bilməliyəm. Qiymət həcmdən, arxitektura mürəkkəbliyindən və Bakıdan məsafədən asılıdır."
RU: "Для точного расчёта нужно знать тип объекта и состояние стен. Цена зависит от объёма, сложности и расстояния от Баку."

FINAL GOAL: After getting service, property type, wall condition and location —
ask for a photo/PDF of the building and phone number for a free engineer visit (pulsuz ölçü / бесплатный замер).

CONVERSATION STATE will be passed to you. Respond naturally and guide the user through the flow.
"""

# ─── SESSION STORAGE (in-memory) ──────────────────────────────────────────────
sessions = {}

def get_session(phone: str) -> dict:
    if phone not in sessions:
        sessions[phone] = {
            "step": 0,
            "language": None,
            "service": None,
            "property_type": None,
            "wall_condition": None,
            "location": None,
            "history": []
        }
    return sessions[phone]

# ─── WHATSAPP SEND HELPERS ─────────────────────────────────────────────────────
def _send(to: str, payload: dict):
    url = f"https://graph.facebook.com/v19.0/{WHATSAPP_PHONE_ID}/messages"
    payload["messaging_product"] = "whatsapp"
    payload["to"] = to
    requests.post(url,
                  headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}",
                            "Content-Type": "application/json"},
                  json=payload)

def send_text(to: str, text: str):
    _send(to, {"type": "text", "text": {"body": text}})

def send_buttons(to: str, body: str, buttons: list):
    _send(to, {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                    for b in buttons
                ]
            }
        }
    })

def send_list(to: str, header: str, body: str, button_label: str, rows: list):
    _send(to, {
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": header},
            "body": {"text": body},
            "action": {
                "button": button_label,
                "sections": [{"title": "Seçim / Выбор", "rows": rows}]
            }
        }
    })

# ─── AI RESPONSE (Gemini) ──────────────────────────────────────────────────────
def ask_gemini(session: dict, user_message: str) -> str:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=SYSTEM_PROMPT
    )
    session["history"].append({"role": "user", "parts": [user_message]})
    chat = model.start_chat(history=session["history"][:-1])
    response = chat.send_message(user_message)
    reply = response.text
    session["history"].append({"role": "model", "parts": [reply]})
    return reply

# ─── MAIN FLOW ─────────────────────────────────────────────────────────────────
def handle_message(phone: str, user_input: str, session: dict):
    step = session["step"]
    lang = session["language"]

    # STEP 0 — First contact → greeting + language buttons
    if step == 0:
        send_text(phone,
            "Salam! *MFN*-ə xoş gəlmisiniz. 🇦🇿\n"
            "Здравствуйте! Добро пожаловать в *MFN*. 🇷🇺"
        )
        send_buttons(phone,
            "Zəhmət olmasa dilinizi seçin / Пожалуйста, выберите язык:",
            [
                {"id": "lang_az", "title": "🇦🇿 Azərbaycan"},
                {"id": "lang_ru", "title": "🇷🇺 Русский"}
            ]
        )
        session["step"] = 1
        return

    # STEP 1 — Language chosen → service selection
    if step == 1:
        if "Azərbaycan" in user_input or "lang_az" in user_input:
            session["language"] = "az"
            send_buttons(phone, "Sizə hansı xidmət lazımdır?", [
                {"id": "svc_full",     "title": "Tam fasad"},
                {"id": "svc_material", "title": "Yalnız material"},
                {"id": "svc_consult",  "title": "Mühəndis məsləhəti"}
            ])
        else:
            session["language"] = "ru"
            send_buttons(phone, "Какая услуга вас интересует?", [
                {"id": "svc_full",     "title": "Фасад под ключ"},
                {"id": "svc_material", "title": "Только материал"},
                {"id": "svc_consult",  "title": "Консультация инженера"}
            ])
        session["step"] = 2
        return

    # STEP 2 — Service chosen → property type
    if step == 2:
        session["service"] = user_input
        if session["language"] == "az":
            send_buttons(phone, "Obyektinizin növünü seçin:", [
                {"id": "obj_villa",  "title": "Həyət evi / Villa"},
                {"id": "obj_apart",  "title": "Çoxmərtəbəli bina"},
                {"id": "obj_other",  "title": "Kommersiya / Renovasiya"}
            ])
        else:
            send_buttons(phone, "Выберите тип вашего объекта:", [
                {"id": "obj_villa",  "title": "Частный дом / Вилла"},
                {"id": "obj_apart",  "title": "Многоэтажное здание"},
                {"id": "obj_other",  "title": "Коммерческое / Реновация"}
            ])
        session["step"] = 3
        return

    # STEP 3 — Property chosen → wall condition
    if step == 3:
        session["property_type"] = user_input
        if session["language"] == "az":
            send_buttons(phone, "Divarlarınızın hazırkı vəziyyəti?", [
                {"id": "wall_brick",     "title": "Kərpic / Kubik"},
                {"id": "wall_concrete",  "title": "Beton"},
                {"id": "wall_old",       "title": "Suvaq / Köhnə fasad"}
            ])
        else:
            send_buttons(phone, "Текущее состояние стен?", [
                {"id": "wall_brick",     "title": "Кирпич / Кубик"},
                {"id": "wall_concrete",  "title": "Бетон"},
                {"id": "wall_old",       "title": "Штукатурка / Старый фасад"}
            ])
        session["step"] = 4
        return

    # STEP 4 — Wall chosen → location
    if step == 4:
        session["wall_condition"] = user_input
        if session["language"] == "az":
            send_buttons(phone, "Obyektiniz harada yerləşir?", [
                {"id": "loc_baku",   "title": "📍 Bakı / Abşeron"},
                {"id": "loc_region", "title": "🚛 Region"}
            ])
        else:
            send_buttons(phone, "Где находится ваш объект?", [
                {"id": "loc_baku",   "title": "📍 Баку / Абшерон"},
                {"id": "loc_region", "title": "🚛 Регион"}
            ])
        session["step"] = 5
        return

    # STEP 5 — Location chosen → price info + CTA
    if step == 5:
        session["location"] = user_input
        if session["language"] == "az":
            send_text(phone,
                "❗ *Qiymət haqqında:*\n"
                "Dəqiq rəqəm mühəndisimiz ölçü götürdükdən sonra hesablanır.\n"
                "*MFN* panellərinin qiyməti həcm, mürəkkəblik və məsafədən asılıdır."
            )
            send_text(phone,
                "✅ *Məlumatlar üçün təşəkkür edirik!*\n\n"
                "Dəqiq hesablama üçün:\n"
                "1️⃣ Binanın *şəklini* və ya *PDF layihəsini* göndərin\n"
                "2️⃣ Əlaqə nömrənizi təsdiqləyin\n\n"
                "Mühəndisimiz *pulsuz ölçü* üçün sizinlə əlaqə saxlayacaq. 📐"
            )
        else:
            send_text(phone,
                "❗ *О цене:*\n"
                "Точная сумма рассчитывается инженером после замера.\n"
                "Стоимость *MFN* зависит от объёма, сложности и расстояния."
            )
            send_text(phone,
                "✅ *Спасибо за информацию!*\n\n"
                "Для точного расчёта:\n"
                "1️⃣ Пришлите *фото* или *PDF-проект* здания\n"
                "2️⃣ Подтвердите ваш номер телефона\n\n"
                "Наш инженер свяжется с вами для *бесплатного замера*. 📐"
            )
        session["step"] = 6
        return

    # STEP 6+ — Free conversation with AI (Gemini handles everything)
    context = (
        f"User language: {session['language']}\n"
        f"Service: {session['service']}\n"
        f"Property: {session['property_type']}\n"
        f"Wall: {session['wall_condition']}\n"
        f"Location: {session['location']}\n"
        f"User says: {user_input}"
    )
    reply = ask_gemini(session, context)
    send_text(phone, reply)

# ─── WEBHOOK ───────────────────────────────────────────────────────────────────
@app.route("/webhook", methods=["GET"])
def verify():
    if (request.args.get("hub.mode") == "subscribe" and
            request.args.get("hub.verify_token") == VERIFY_TOKEN):
        return request.args.get("hub.challenge"), 200
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True)
    try:
        message = data["entry"][0]["changes"][0]["value"]["messages"][0]
        phone = message["from"]
        msg_type = message["type"]

        if msg_type == "text":
            user_input = message["text"]["body"]
        elif msg_type == "interactive":
            itype = message["interactive"]["type"]
            if itype == "button_reply":
                user_input = message["interactive"]["button_reply"]["title"]
            elif itype == "list_reply":
                user_input = message["interactive"]["list_reply"]["title"]
            else:
                user_input = ""
        else:
            user_input = ""

        if user_input:
            session = get_session(phone)
            handle_message(phone, user_input, session)

    except (KeyError, IndexError, TypeError):
        pass

    return jsonify({"status": "ok"})

@app.route("/", methods=["GET"])
def health():
    return "MFN Agent is running ✅", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
