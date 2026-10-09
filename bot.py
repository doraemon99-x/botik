import os
import re
import asyncio
import subprocess
import requests
from aiohttp import web
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeVideo
from pymediainfo import MediaInfo

# ==========================================
# KONFIGURASI BOT (Environment Variables / Default)
# ==========================================
API_ID = int(os.getenv("API_ID", "1916950"))
API_HASH = os.getenv("API_HASH", "9e268fee501ad809e4f5f598adcb970c")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8988516745:AAEQnDxcgUY5c_ip44PlxltGf7kdYHg9rwY")
PORT = int(os.getenv("PORT", "8000"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(BASE_DIR, "downloads", "bot_temp")
os.makedirs(TEMP_DIR, exist_ok=True)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

client = TelegramClient(os.path.join(BASE_DIR, "bot_session"), API_ID, API_HASH)


# ==========================================
# DUMMY HTTP SERVER (UNTUK HEALTH CHECK KOYEB)
# ==========================================
async def start_healthcheck_server():
    """Server HTTP ringan agar platform Cloud seperti Koyeb/Render mendeteksi bot aktif."""
    async def handle_ping(request):
        return web.json_response({
            "status": "healthy",
            "bot": "NgunduhMantuh_bot",
            "message": "Bot TikTok Downloader is running"
        })

    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🌐 Health check HTTP server aktif di port {PORT}")


# ==========================================
# HELPER SCRAPER TIKTOK
# ==========================================
def resolve_tiktok_url(url):
    """Mengikuti redirect URL pendek (vt.tiktok.com atau vm.tiktok.com)."""
    url = url.strip()
    if "vt.tiktok.com" in url or "vm.tiktok.com" in url:
        try:
            r = requests.get(url, headers=DEFAULT_HEADERS, allow_redirects=True, timeout=10)
            return r.url
        except Exception:
            return url
    return url


def extract_media_id(url):
    """Mengekstrak ID postingan dari URL."""
    m = re.search(r'/(?:video|photo)/(\d+)', url)
    if m:
        return m.group(1)
    m = re.search(r'/(\d{15,25})', url)
    if m:
        return m.group(1)
    cleaned = re.sub(r'\W+', '', url)
    return cleaned[-12:] if cleaned else "media"


def get_data_ssstik(url, media_id):
    """Scraper ssstik.io untuk video atau foto."""
    try:
        s = requests.Session()
        s.headers.update(DEFAULT_HEADERS)
        s.get("https://ssstik.io/en", timeout=10)
        res = s.post(
            "https://ssstik.io/abc?url=dl",
            data={"id": url, "locale": "en", "tt": "0"},
            timeout=15
        )
        html = res.text

        # Cek apakah postingan foto/carousel
        img_links = re.findall(r'href=[\x22\x27](https://tikcdn\.io/ssstik/aHR0[^\x22\x27]+)[\x22\x27]', html)
        if img_links:
            return {"id": media_id, "images": img_links, "type": "photo"}

        # Cek apakah video
        video_links = [
            l for l in re.findall(r'href=[\x22\x27](https://tikcdn\.io/ssstik/[^\x22\x27]+)[\x22\x27]', html)
            if "/m/" not in l
        ]
        if video_links:
            return {"id": media_id, "play": video_links[0], "type": "video"}
    except Exception:
        pass
    return None


def get_data_musicaldown(url, media_id):
    """Fallback scraper musicaldown.com."""
    try:
        s = requests.Session()
        s.headers.update(DEFAULT_HEADERS)
        r = s.get("https://musicaldown.com/en", timeout=10)
        inputs = dict(re.findall(r'<input[^>]*name=[\x22\x27]([^\x22\x27]+)[\x22\x27][^>]*value=[\x22\x27]([^\x22\x27]*)[\x22\x27]', r.text))
        post_data = inputs.copy()
        for name in re.findall(r'<input[^>]*name=[\x22\x27]([^\x22\x27]+)[\x22\x27]', r.text):
            if name not in post_data:
                post_data[name] = url
        r_post = s.post(
            "https://musicaldown.com/download",
            data=post_data,
            headers={**DEFAULT_HEADERS, "Referer": "https://musicaldown.com/en"},
            timeout=15
        )
        links = [l for l in re.findall(r'href=[\x22\x27](https?://[^\s\x22\x27]+)[\x22\x27]', r_post.text) if "muscdn.app" in l]
        if links:
            return {"id": media_id, "play": links[0], "type": "video"}
    except Exception:
        pass
    return None


def fetch_tiktok_media_info(url):
    """Mengambil informasi media menggunakan multi-backend scraper."""
    resolved_url = resolve_tiktok_url(url)
    media_id = extract_media_id(resolved_url)

    # Coba SSSTik
    data = get_data_ssstik(url, media_id)
    if not data and resolved_url != url:
        data = get_data_ssstik(resolved_url, media_id)
    if data:
        return data

    # Fallback MusicalDown
    data = get_data_musicaldown(url, media_id)
    if not data and resolved_url != url:
        data = get_data_musicaldown(resolved_url, media_id)
    if data:
        return data

    return None


def download_binary(url, dest_path):
    """Mendownload file binary ke path lokal."""
    headers = {**DEFAULT_HEADERS, "Referer": "https://www.tiktok.com/"}
    r = requests.get(url, headers=headers, stream=True, timeout=60)
    if r.status_code != 200:
        return False
    with open(dest_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=128 * 1024):
            if chunk:
                f.write(chunk)
    return True


def get_video_metadata(file_path):
    """Mendapatkan metadata durasi, lebar, dan tinggi video."""
    try:
        media_info = MediaInfo.parse(file_path)
        for track in media_info.tracks:
            if track.track_type == "Video":
                duration = int(track.duration / 1000) if track.duration else 0
                width = track.width if track.width else 0
                height = track.height if track.height else 0
                return duration, width, height
    except Exception:
        pass
    return 0, 0, 0


def generate_thumbnail(video_path):
    """Membuat thumbnail video dengan ffmpeg."""
    thumb_path = video_path + "_thumb.jpg"
    command = [
        "ffmpeg",
        "-y",
        "-ss", "00:00:02",
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        thumb_path
    ]
    try:
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except Exception:
        pass
    return None


# ==========================================
# TELEGRAM BOT HANDLERS
# ==========================================

@client.on(events.NewMessage(pattern=r'^/start'))
async def start_handler(event):
    welcome_text = (
        "👋 **Selamat datang di TikTok Downloader Bot!**\n\n"
        "Kirimkan link postingan TikTok (Video atau Foto Carousel) ke sini, "
        "dan saya akan mengunduh serta mengirimkannya tanpa watermark.\n\n"
        "📌 **Format yang didukung:**\n"
        "• `https://vt.tiktok.com/...`\n"
        "• `https://www.tiktok.com/@user/video/...`\n"
        "• `https://www.tiktok.com/@user/photo/...`\n\n"
        "_Silakan tempel (paste) link TikTok Anda sekarang!_"
    )
    await event.reply(welcome_text)


@client.on(events.NewMessage)
async def message_handler(event):
    text = event.raw_text.strip()
    if text.startswith("/start"):
        return

    # Cari URL TikTok di dalam pesan
    pattern = r'https?://(?:(?:[a-zA-Z0-9_-]+\.)?tiktok\.com)/\S+'
    match = re.search(pattern, text)
    if not match:
        return

    tiktok_url = match.group(0)
    status_msg = await event.reply("⏳ **Sedang memproses link TikTok...**")

    created_files = []

    try:
        data = await asyncio.to_thread(fetch_tiktok_media_info, tiktok_url)
        if not data:
            await status_msg.edit(
                "❌ **Gagal mengambil media dari link TikTok tersebut.**\n"
                "Pastikan postingan bersifat publik dan link masih aktif."
            )
            return

        media_id = data.get("id", "media")
        media_type = data.get("type")

        # ----------------------------------------------------
        # KASUS 1: VIDEO TIKTOK
        # ----------------------------------------------------
        if media_type == "video" and data.get("play"):
            await status_msg.edit("⬇ **Sedang mendownload video...**")
            video_path = os.path.join(TEMP_DIR, f"{media_id}_{event.id}.mp4")
            created_files.append(video_path)

            ok = await asyncio.to_thread(download_binary, data["play"], video_path)
            if not ok or not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
                await status_msg.edit("❌ **Gagal mendownload file video dari server.**")
                return

            await status_msg.edit("⬆ **Sedang mengunggah video ke Telegram...**")

            duration, width, height = await asyncio.to_thread(get_video_metadata, video_path)
            thumb_path = await asyncio.to_thread(generate_thumbnail, video_path)
            if thumb_path:
                created_files.append(thumb_path)

            attributes = []
            if duration or width or height:
                attributes.append(DocumentAttributeVideo(
                    duration=duration,
                    w=width,
                    h=height,
                    supports_streaming=True
                ))

            caption = (
                f"🎬 **TikTok Video** (ID: `{media_id}`)\n"
                f"🔗 [Link TikTok]({tiktok_url})"
            )

            await client.send_file(
                event.chat_id,
                file=video_path,
                caption=caption,
                thumb=thumb_path,
                attributes=attributes,
                reply_to=event.message.id
            )
            await status_msg.delete()

        # ----------------------------------------------------
        # KASUS 2: POSTINGAN FOTO / CAROUSEL
        # ----------------------------------------------------
        elif media_type == "photo" and data.get("images"):
            images = data["images"]
            total_img = len(images)
            await status_msg.edit(f"⬇ **Sedang mendownload {total_img} foto...**")

            img_paths = []
            for idx, img_url in enumerate(images, start=1):
                img_path = os.path.join(TEMP_DIR, f"{media_id}_{event.id}_{idx}.jpg")
                created_files.append(img_path)
                ok = await asyncio.to_thread(download_binary, img_url, img_path)
                if ok and os.path.exists(img_path) and os.path.getsize(img_path) > 0:
                    img_paths.append(img_path)

            if not img_paths:
                await status_msg.edit("❌ **Gagal mendownload gambar-gambar postingan.**")
                return

            await status_msg.edit(f"⬆ **Mengunggah {len(img_paths)} foto ke Telegram...**")

            caption = (
                f"📸 **TikTok Photo Album** ({len(img_paths)} Foto)\n"
                f"🔗 [Link TikTok]({tiktok_url})"
            )

            await client.send_file(
                event.chat_id,
                file=img_paths,
                caption=caption,
                reply_to=event.message.id
            )
            await status_msg.delete()

        else:
            await status_msg.edit("❌ **Format media TikTok tidak dikenali.**")

    except Exception as e:
        print(f"Error handling message: {e}")
        try:
            await status_msg.edit(f"❌ **Terjadi kesalahan:** {e}")
        except Exception:
            pass

    finally:
        for fpath in created_files:
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except Exception:
                    pass


# ==========================================
# MAIN ENTRYPOINT
# ==========================================
async def start_bot():
    # Jalankan HTTP Health Check Server untuk Koyeb / Cloud
    await start_healthcheck_server()

    # Jalankan Telegram Bot Client
    await client.start(bot_token=BOT_TOKEN)

    me = await client.get_me()
    print("=" * 45)
    print("🤖 TIKTOK DOWNLOADER TELEGRAM BOT")
    print(f"✅ Bot Berhasil Berjalan!")
    print(f"   Nama    : {me.first_name}")
    print(f"   Username: @{me.username}")
    print(f"   Bot ID  : {me.id}")
    print("=" * 45)
    print("Menunggu pesan link TikTok... (Ctrl+C untuk stop)")

    await client.run_until_disconnected()


def main():
    print("Menghubungkan ke Telegram...")
    client.loop.run_until_complete(start_bot())


if __name__ == '__main__':
    main()
