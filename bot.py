import os
import datetime
import json
import logging
import urllib.parse

# Library untuk environment variables
from dotenv import load_dotenv

# Library untuk AI (Gemini)
import google.generativeai as genai

# Library untuk Google Calendar
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Library untuk Telegram Bot
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)

# --- KONFIGURASI AWAL ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_FILE = "token.json"
CREDS_FILE = "credentials.json"


# --- FUNGSI-FUNGSI UTAMA ---

def get_calendar_service():
    """
    Fungsi otentikasi Google Calendar yang andal untuk server,
    membangun kredensial langsung dari environment variables.
    """
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")

    # Pastikan semua variabel ada di environment server
    if not all([client_id, client_secret, refresh_token]):
        logger.error("Variabel Google OAuth (CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN) tidak ditemukan di server.")
        raise ValueError("Konfigurasi Variabel Google di server tidak lengkap.")

    try:
        # Membuat objek Credentials secara langsung dengan semua data yang dibutuhkan
        creds = Credentials(
            token=None,  # Access token akan di-refresh
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )

        # Me-refresh token untuk mendapatkan access token yang valid
        creds.refresh(Request())
        
        return build("calendar", "v3", credentials=creds)

    except Exception as e:
        logger.error(f"Gagal total saat otentikasi dengan Google: {e}")
        # Jika gagal di sini, berarti ada masalah fundamental dengan kredensial
        raise

def parse_schedule_with_ai(text: str) -> dict:
    """Mem-parsing teks menggunakan Gemini AI."""
    today = datetime.date.today().strftime("%Y-%m-%d")
    prompt = f"""
    Anda adalah asisten cerdas. Ekstrak detail dari teks berikut:
    Tanggal referensi hari ini: {today}. Teks pengguna: "{text}"
    Tugas Anda:
    1. Ekstrak: judul acara, lokasi (jika ada), tanggal (format YYYY-MM-DD), dan waktu (format 24 jam HH:MM:SS). Judul acara harus spesifik.
    2. Tentukan Kategori dari daftar: 'drone', 'drone fpv', 'cinematic', 'short movie', 'foto', atau 'Lainnya'.
    Kembalikan HANYA format JSON yang valid. Jika tidak bisa, kembalikan JSON kosong.
    """
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        cleaned_response = response.text.replace("```json", "").replace("```", "").strip()
        logger.info(f"AI Response: {cleaned_response}")
        return json.loads(cleaned_response)
    except Exception as e:
        logger.error(f"Error parsing with AI: {e}")
        return {}


# --- HANDLER UNTUK PERINTAH TELEGRAM ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler untuk perintah /start."""
    await update.message.reply_text(
        "Halo! Saya bot penjadwalan Anda.\n"
        "Fitur:\n"
        "/jadwal_hari_ini - Lihat jadwal hari ini\n"
        "/hapus_pilih - Hapus jadwal tertentu\n"
        "/hapus_semua - Hapus semua jadwal mendatang"
    )

async def get_schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler untuk /jadwal_hari_ini, dengan filter."""
    try:
        service = get_calendar_service()
        tz = datetime.datetime.now().astimezone().tzinfo
        time_min = datetime.datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        await update.message.reply_text("Mencari jadwal...")
        events_result = service.events().list(
            calendarId='primary', timeMin=time_min, maxResults=20,
            singleEvents=True, orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        events = [event for event in events if "happy birthday" not in event.get('summary', '').lower()]

        if not events:
            await update.message.reply_text("Tidak ada jadwal mendatang yang ditemukan (selain ulang tahun).")
            return
            
        message = "🗓️ **Jadwal Anda Berikutnya:**\n\n"
        for event in events[:10]:
            start = event['start'].get('dateTime', event['start'].get('date'))
            dt_object = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
            local_time = dt_object.astimezone().strftime("%d %b %Y, %H:%M")
            message += f"- **{event['summary']}**\n  _{local_time}_\n"
            
        await update.message.reply_text(message, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error getting schedule: {e}")
        await update.message.reply_text("Maaf, terjadi kesalahan saat mengambil jadwal.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler untuk pesan teks biasa (membuat jadwal)."""
    user_text = update.message.text
    chat_id = update.message.chat_id
    await context.bot.send_message(chat_id, text="Oke, saya proses dulu ya...")
    schedule_data = parse_schedule_with_ai(user_text)

    # Cek data penting dari AI
    if not schedule_data or 'tanggal' not in schedule_data or 'waktu' not in schedule_data:
        await context.bot.send_message(chat_id, text="Maaf, saya tidak bisa menentukan tanggal atau waktu.")
        return

    # Fleksibel dalam mencari kunci judul
    judul_acara = schedule_data.get('judul') or schedule_data.get('judul_acara')
    
    if not judul_acara:
        await context.bot.send_message(chat_id, text="Maaf, saya tidak bisa menentukan judul acara dari permintaan Anda.")
        return

    try:
        service = get_calendar_service()
        start_time_obj = datetime.datetime.fromisoformat(f"{schedule_data['tanggal']}T{schedule_data['waktu']}")
        end_time_obj = start_time_obj + datetime.timedelta(hours=1)
        
        description = f"Kategori: {schedule_data.get('kategori', 'Lainnya')}"
        location = schedule_data.get('lokasi', '')
        
        if location:
            maps_link = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote_plus(location)}"
            description += f"\n\n📍 Buka Lokasi di Peta: {maps_link}"
            
        event = {
            'summary': judul_acara, # Menggunakan judul yang sudah ditemukan
            'location': location,
            'description': description,
            'start': {'dateTime': start_time_obj.isoformat(), 'timeZone': 'Asia/Jakarta'},
            'end': {'dateTime': end_time_obj.isoformat(), 'timeZone': 'Asia/Jakarta'},
        }
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        
        tz_jakarta = datetime.timezone(datetime.timedelta(hours=7))
        local_start_time = start_time_obj.replace(tzinfo=tz_jakarta).strftime('%d %b %Y, %H:%M')
        confirmation_text = (
            f"✅ **Berhasil!** Jadwal telah ditambahkan.\n\n"
            f"**Acara:** {created_event['summary']}\n"
            f"**Waktu:** {local_start_time}"
        )
        if location:
             confirmation_text += f"\n**Lokasi:** {location}"
             
        await context.bot.send_message(chat_id, text=confirmation_text, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error creating calendar event: {e}")
        await context.bot.send_message(chat_id, text="Maaf, terjadi kesalahan saat menyimpan ke kalender.")

# --- FITUR HAPUS JADWAL ---

async def delete_selective_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Menampilkan daftar jadwal untuk dipilih dan dihapus, dengan filter."""
    service = get_calendar_service()
    now = datetime.datetime.utcnow().isoformat() + 'Z'
    events_result = service.events().list(
        calendarId='primary', timeMin=now, maxResults=20,
        singleEvents=True, orderBy='startTime'
    ).execute()
    events = events_result.get('items', [])

    events = [event for event in events if "happy birthday" not in event.get('summary', '').lower()]

    if not events:
        await update.message.reply_text("Tidak ada jadwal mendatang untuk dihapus (selain ulang tahun).")
        return

    keyboard = []
    for event in events[:10]:
        event_id = event['id']
        event_summary = event['summary']
        button = [InlineKeyboardButton(f"❌ {event_summary}", callback_data=f"delete_event_{event_id}")]
        keyboard.append(button)
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Pilih jadwal yang ingin Anda hapus:', reply_markup=reply_markup)

async def delete_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Meminta konfirmasi untuk menghapus semua jadwal (kecuali ulang tahun)."""
    keyboard = [[
        InlineKeyboardButton("🔴 Ya, Hapus Semua", callback_data="confirm_delete_all"),
        InlineKeyboardButton("Batal", callback_data="cancel_delete")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "⚠️ **PERINGATAN!** Anda yakin ingin menghapus SEMUA jadwal mendatang (selain ulang tahun)? Aksi ini tidak bisa dibatalkan.",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Menangani aksi dari tombol inline."""
    query = update.callback_query
    await query.answer()
    
    service = get_calendar_service()
    
    if query.data.startswith("delete_event_"):
        event_id = query.data.split("delete_event_")[1]
        try:
            service.events().delete(calendarId='primary', eventId=event_id).execute()
            await query.edit_message_text(text=f"Jadwal berhasil dihapus.")
        except Exception as e:
            logger.error(f"Error deleting event: {e}")
            await query.edit_message_text(text="Gagal menghapus jadwal.")
            
    elif query.data == "confirm_delete_all":
        await query.edit_message_text(text="Sedang memproses, mohon tunggu...")
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        events_result = service.events().list(
            calendarId='primary', timeMin=now, singleEvents=True
        ).execute()
        events = events_result.get('items', [])

        events = [event for event in events if "happy birthday" not in event.get('summary', '').lower()]
        
        count = 0
        for event in events:
            try:
                service.events().delete(calendarId='primary', eventId=event['id']).execute()
                count += 1
            except Exception as e:
                logger.error(f"Could not delete event {event['id']}: {e}")
        
        await query.edit_message_text(text=f"✅ Selesai! {count} jadwal mendatang telah dihapus.")

    elif query.data == "cancel_delete":
        await query.edit_message_text(text="Aksi dibatalkan.")


# --- FUNGSI UTAMA UNTUK MENJALANKAN BOT ---

def main() -> None:
    """Fungsi utama untuk menjalankan bot."""
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("jadwal_hari_ini", get_schedule_command))
    application.add_handler(CommandHandler("hapus_semua", delete_all_command))
    application.add_handler(CommandHandler("hapus_pilih", delete_selective_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot is running...")
    application.run_polling()


if __name__ == "__main__":
    main()
