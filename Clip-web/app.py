import html
import json
import os
import re
import tempfile
import time
from types import SimpleNamespace

import streamlit as st
import streamlit.components.v1 as components
from google import genai
from google.genai import types
from youtube_transcript_api import YouTubeTranscriptApi

# ----------------------------------------------------------------------------
# Konfigurasi halaman
# ----------------------------------------------------------------------------
st.set_page_config(
    page_title="ClipHook AI",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"], .stApp { font-family: 'Inter', sans-serif; }
.stApp { background: radial-gradient(1200px 600px at 10% -10%, #1e1b4b 0%, #0b0b14 55%) fixed; }
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding-top: 2rem; max-width: 1100px; }

.hero { text-align: center; padding: 1.5rem 0 1rem; }
.hero h1 {
    font-size: 3rem; font-weight: 800; margin: 0; letter-spacing: -1px;
    background: linear-gradient(90deg, #a78bfa, #60a5fa, #34d399);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.hero p { color: #9ca3af; font-size: 1.05rem; margin-top: .4rem; }
.badge {
    display: inline-block; padding: 3px 12px; border-radius: 999px; font-size: .75rem;
    background: rgba(167,139,250,.15); color: #c4b5fd; border: 1px solid rgba(167,139,250,.35);
    margin-bottom: .8rem; font-weight: 600;
}

.card {
    background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.09);
    border-radius: 16px; padding: 18px 20px; margin-bottom: 10px;
    backdrop-filter: blur(8px);
}
.card .meta { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; }
.pill {
    font-size: .75rem; font-weight: 700; padding: 3px 10px; border-radius: 999px;
    background: rgba(96,165,250,.15); color: #93c5fd; border: 1px solid rgba(96,165,250,.3);
    text-decoration: none !important;
}
.pill.score { background: rgba(52,211,153,.15); color: #6ee7b7; border-color: rgba(52,211,153,.3); }
.card h4 { margin: 4px 0 6px; font-size: 1.05rem; color: #f3f4f6; font-weight: 700; }
.card p { margin: 0; color: #cbd5e1; line-height: 1.6; font-size: .95rem; }
.hooktext {
    font-size: 1.5rem; font-weight: 800; color: #fff; line-height: 1.25; text-align: center;
    padding: 6px 0; text-transform: uppercase;
}
.section-title { font-size: 1.35rem; font-weight: 800; color: #f9fafb; margin: 1.8rem 0 .6rem; }
.section-sub { color: #9ca3af; font-size: .9rem; margin-top: -.4rem; margin-bottom: .8rem; }

.stButton > button[kind="primary"] {
    background: linear-gradient(90deg, #7c3aed, #2563eb); border: 0; color: white;
    font-weight: 700; border-radius: 12px; padding: .65rem 1rem; width: 100%;
}
.stButton > button[kind="primary"]:hover { filter: brightness(1.15); }
.stTextInput input, .stTextArea textarea {
    background: rgba(255,255,255,.05) !important; border-radius: 12px !important;
    border: 1px solid rgba(255,255,255,.12) !important; color: #f3f4f6 !important;
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ----------------------------------------------------------------------------
# Helper
# ----------------------------------------------------------------------------
def copy_button(text: str, label: str = "📋 Salin", height: int = 46):
    """Tombol salin (clipboard) berbasis JS, dengan fallback execCommand."""
    payload = json.dumps(text).replace("</", "<\\/")
    page = """
    <style>
      body { margin:0; font-family: Inter, system-ui, sans-serif; }
      button {
        width:100%; height:38px; cursor:pointer; border-radius:10px; font-weight:700;
        font-size:13px; color:#c4b5fd; background:rgba(167,139,250,.12);
        border:1px solid rgba(167,139,250,.4); transition:.15s;
      }
      button:hover { background:rgba(167,139,250,.28); }
      button.ok { color:#6ee7b7; border-color:rgba(52,211,153,.5); background:rgba(52,211,153,.12); }
    </style>
    <button id="b">__LABEL__</button>
    <script>
      const txt = __PAYLOAD__;
      const label = "__LABEL__";
      const btn = document.getElementById('b');
      function fallback() {
        const t = document.createElement('textarea');
        t.value = txt; document.body.appendChild(t); t.select();
        try { document.execCommand('copy'); } catch (e) {}
        document.body.removeChild(t);
      }
      btn.onclick = async () => {
        try { await navigator.clipboard.writeText(txt); } catch (e) { fallback(); }
        btn.textContent = '✅ Tersalin!'; btn.classList.add('ok');
        setTimeout(() => { btn.textContent = label; btn.classList.remove('ok'); }, 1600);
      };
    </script>
    """.replace("__PAYLOAD__", payload).replace("__LABEL__", label)
    components.html(page, height=height)


def extract_video_id(url: str):
    patterns = [
        r"(?:youtu\.be/)([\w-]{11})",
        r"(?:v=)([\w-]{11})",
        r"(?:embed/|shorts/|live/|v/)([\w-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    if re.fullmatch(r"[\w-]{11}", url.strip()):
        return url.strip()
    return None


def fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def to_seconds(ts: str) -> int:
    try:
        parts = [int(float(x)) for x in str(ts).strip().split(":")]
    except Exception:
        return 0
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return sec


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_transcript(video_id: str):
    """Ambil transkrip (prioritas Indonesia lalu Inggris). Return (list_of_dict, bahasa)."""
    api = YouTubeTranscriptApi()
    try:
        fetched = api.fetch(video_id, languages=["id", "en"])
    except Exception as first_err:
        # Fallback: ambil transkrip apa pun yang tersedia
        try:
            tlist = api.list(video_id)
            chosen = next(iter(tlist))
            fetched = chosen.fetch()
        except Exception:
            raise first_err
    items = [
        {"text": s.text, "start": float(s.start), "duration": float(s.duration)}
        for s in fetched
    ]
    return items, getattr(fetched, "language_code", "?")


def parse_manual_transcript(raw: str):
    """Parser transkrip manual. Mendukung beberapa format hasil salinan:
    1) '0.070 detik teks'  (format M.SS + menit diulang, hasil salin dari HP/YouTube versi tertentu)
    2) '[mm:ss] teks' atau '00:12 teks'
    3) timestamp '0:12' di baris sendiri, teks di baris berikutnya (format desktop YouTube)
    4) teks polos tanpa timestamp
    """
    pat_detik = re.compile(r"^(\d+)\.(\d{2})\d*\s*detik\s*(.*)$", re.IGNORECASE)
    pat_clock = re.compile(r"^\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s*(.*)$")
    items, last, pending = [], 0.0, None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = pat_detik.match(line)
        if m:
            start = int(m.group(1)) * 60 + int(m.group(2))
            text = m.group(3).strip()
            pending = None
        else:
            m = pat_clock.match(line)
            if m:
                start = to_seconds(m.group(1))
                text = m.group(2).strip()
                if not text:  # timestamp di baris sendiri
                    pending = float(start)
                    continue
                pending = None
            else:
                start = pending if pending is not None else last
                text = line
                pending = None
        if text:
            last = float(start)
            items.append({"text": text, "start": last, "duration": 0.0})
    return items


TEXT_EXTS = (".txt", ".srt", ".vtt")
MEDIA_MIME = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".ogg": "audio/ogg", ".flac": "audio/flac",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
}


def parse_subtitle(raw: str):
    """Parser file subtitle .srt / .vtt -> list segmen dengan start (detik)."""
    items = []
    pat = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,]\d{1,3}\s*-->")
    for block in re.split(r"\n\s*\n", raw.replace("\r", "")):
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        for i, line in enumerate(lines):
            m = pat.search(line)
            if m:
                start = int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                text = " ".join(re.sub(r"<[^>]+>", "", x) for x in lines[i + 1:]).strip()
                if text:
                    items.append({"text": text, "start": float(start), "duration": 0.0})
                break
    return items


def upload_media(api_key: str, uploaded, say=None):
    """Upload file audio/video ke Gemini Files API dan tunggu sampai siap dipakai."""
    say = say or (lambda *_: None)
    client = genai.Client(api_key=api_key)
    ext = os.path.splitext(uploaded.name)[1].lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(uploaded.getbuffer())
        path = tmp.name
    try:
        say("📤 Mengunggah file ke Gemini...")
        f = client.files.upload(file=path, config=types.UploadFileConfig(mime_type=MEDIA_MIME.get(ext)))
        for n in range(200):  # maks ~10 menit menunggu pemrosesan
            state = getattr(f.state, "name", str(f.state))
            if state != "PROCESSING":
                break
            say(f"⏳ Gemini sedang memproses file... ({n * 3} detik)")
            time.sleep(3)
            f = client.files.get(name=f.name)
        say("✅ File siap dianalisis.")
        if getattr(f.state, "name", str(f.state)) == "FAILED":
            raise RuntimeError("Gemini gagal memproses file media ini.")
        return f
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def delete_media(api_key: str, media):
    try:
        genai.Client(api_key=api_key).files.delete(name=media.name)
    except Exception:
        pass


def build_transcript_text(items, chunk_seconds: int = 12, max_chars: int = 350_000) -> str:
    """Gabungkan potongan pendek jadi baris '[mm:ss] teks' agar hemat token."""
    lines, buf, buf_start = [], [], None
    for it in items:
        if buf_start is None:
            buf_start = it["start"]
        buf.append(it["text"].replace("\n", " ").strip())
        if it["start"] - buf_start >= chunk_seconds:
            lines.append(f"[{fmt_time(buf_start)}] {' '.join(buf)}")
            buf, buf_start = [], None
    if buf:
        lines.append(f"[{fmt_time(buf_start)}] {' '.join(buf)}")
    text = "\n".join(lines)
    return text[:max_chars]


SYSTEM_PROMPT = """Kamu adalah editor video short-form senior dan pakar konten viral TikTok/Instagram Reels/YouTube Shorts
untuk pasar Indonesia. Tugasmu membantu video clipper menemukan momen paling berpotensi viral dari transkrip
video panjang (podcast, wawancara, film, dll) lalu membuat hook yang kuat.

Aturan:
- Gunakan HANYA isi transkrip. Jangan mengarang kejadian atau kutipan.
- Timestamp wajib memakai format MM:SS (atau H:MM:SS jika lebih dari 1 jam) dan harus sesuai transkrip.
- Setiap momen sebaiknya berdurasi 20-75 detik, punya awal dan akhir yang utuh, dan bisa berdiri sendiri.
- Prioritaskan: konflik, pengakuan jujur, plot twist, kontroversi, angka mengejutkan, humor, emosi kuat, tips praktis.
- Hook text: maksimal 8 kata, huruf besar boleh, memicu rasa penasaran, cocok dibaca dalam 3 detik pertama.
- Hook audio: 1-2 kalimat narasi pembuka yang natural untuk direkam ulang clipper (gaya bicara santai, bukan kaku).
- Bahasa output: Bahasa Indonesia gaul-santai yang tetap rapi.
- Output HANYA JSON valid tanpa markdown, sesuai skema berikut:

{
  "ringkasan": "ringkasan video 1-2 kalimat",
  "momen_viral": [
    {"mulai": "MM:SS", "selesai": "MM:SS", "judul": "judul singkat momen", "alasan": "kenapa menarik/viral", "skor_viral": 1-10}
  ],
  "hook_text": ["hook 1", "hook 2", "hook 3"],
  "hook_audio": "kalimat narasi pembuka",
  "judul": ["judul 1", "judul 2", "judul 3"],
  "caption": ["caption lengkap + hashtag 1", "caption lengkap + hashtag 2", "caption lengkap + hashtag 3"]
}

Jumlah: tepat 5 momen_viral (urut dari skor tertinggi), tepat 3 hook_text, tepat 1 hook_audio, 3 judul, 3 caption.
Setiap caption berisi kalimat pembuka menarik, ajakan interaksi (CTA), dan 4-6 hashtag relevan."""


FALLBACK_MODELS = ["gemini-3.8-flash", "gemini-flash-latest", "gemini-flash-lite-latest"]
SKIP_WORDS = ("image", "tts", "live", "audio", "embedding", "native", "robotics", "computer", "exp")


def list_flash_models(api_key: str):
    """Tanya Google model Flash apa yang benar-benar tersedia untuk API key ini."""
    client = genai.Client(api_key=api_key)
    names = []
    for m in client.models.list():
        name = m.name.replace("models/", "")
        actions = getattr(m, "supported_actions", None) or []
        if "generateContent" in actions and "flash" in name and not any(w in name for w in SKIP_WORDS):
            names.append(name)
    normal = sorted([n for n in names if "lite" not in n], reverse=True)
    lite = sorted([n for n in names if "lite" in n], reverse=True)
    return normal + lite


def _try_model(client, model_name: str, contents, say=None, low_res=False):
    """Return (response, error). Pakai streaming supaya progres kelihatan; retry bila server sibuk (503)."""
    say = say or (lambda *_: None)
    err, use_low = None, low_res
    for attempt in range(3):
        try:
            say(f"🤖 Mencoba model **{model_name}**... (percobaan {attempt + 1}/3)")
            kwargs = {"temperature": 0.8, "response_mime_type": "application/json"}
            if use_low:  # video: kurangi resolusi frame agar jauh lebih ringan & cepat
                kwargs["media_resolution"] = types.MediaResolution.MEDIA_RESOLUTION_LOW
            text = ""
            for chunk in client.models.generate_content_stream(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(**kwargs),
            ):
                text += getattr(chunk, "text", None) or ""
            if not text.strip():
                raise RuntimeError("AI mengembalikan jawaban kosong (kemungkinan diblokir filter keamanan).")
            return SimpleNamespace(text=text), None
        except Exception as e:
            msg = str(e)
            err = e
            if use_low and ("400" in msg or "INVALID_ARGUMENT" in msg):
                use_low = False
                say("ℹ️ Pengaturan resolusi rendah tidak didukung model ini, mengulang tanpa itu...")
                continue
            if "503" in msg or "UNAVAILABLE" in msg or "overloaded" in msg.lower():
                if attempt == 2:
                    say(f"⚠️ Model **{model_name}** masih sibuk setelah 3 percobaan.")
                    break
                wait = 3 * (attempt + 1)
                say(f"⚠️ Server Google sibuk. Menunggu {wait} detik lalu coba ulang...")
                time.sleep(wait)
                continue
            if "timed out" in msg.lower() or "timeout" in msg.lower() or "504" in msg or "DEADLINE" in msg:
                say(f"⏱️ Model **{model_name}** terlalu lama merespons.")
                return None, e
            if "404" in msg or "NOT_FOUND" in msg:
                say(f"❌ Model **{model_name}** tidak tersedia, lanjut ke model lain.")
                return None, e  # model tidak ada -> coba model lain
            raise
    return None, err


def call_gemini(api_key: str, model: str, transcript_text: str, extra: str, media=None, say=None) -> dict:
    say = say or (lambda *_: None)
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=240_000))
    if media is not None:
        prompt = (
            f"{SYSTEM_PROMPT}\n\nCatatan tambahan dari clipper: {extra or '-'}\n\n"
            "SUMBER: file audio/video terlampir (bukan teks). Dengarkan seluruh isinya dan gunakan "
            "waktu pada media tersebut sebagai timestamp (format MM:SS)."
        )
        contents = [media, prompt]
    else:
        prompt = (
            f"{SYSTEM_PROMPT}\n\nCatatan tambahan dari clipper: {extra or '-'}\n\n"
            f"TRANSKRIP (format [menit:detik] teks):\n{transcript_text}"
        )
        contents = prompt

    tried, resp, last_err, used = [], None, None, None
    # 1) model pilihan user + cadangan
    for m in [model] + [x for x in FALLBACK_MODELS if x != model]:
        tried.append(m)
        resp, err = _try_model(client, m, contents, say, low_res=media is not None)
        if resp is not None:
            used = m
            break
        last_err = err
    # 2) jika semua 404: cari sendiri model Flash yang tersedia untuk key ini
    if resp is None:
        say("🔎 Mencari model Flash yang tersedia untuk API key kamu...")
        try:
            discovered = [x for x in list_flash_models(api_key) if x not in tried][:6]
        except Exception:
            discovered = []
        for m in discovered:
            resp, err = _try_model(client, m, contents, say, low_res=media is not None)
            if resp is not None:
                used = m
                break
            last_err = err
    if resp is None:
        raise last_err

    say(f"✅ Berhasil dengan model **{used}**. Menyusun hasil...")
    st.session_state["model_used"] = used
    raw = (resp.text or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    if "{" in raw and "}" in raw:
        raw = raw[raw.index("{"): raw.rindex("}") + 1]
    return json.loads(raw)


def render_card(title: str, body: str, pills_html: str = ""):
    st.markdown(
        f"""<div class="card"><div class="meta">{pills_html}</div>
        <h4>{html.escape(title)}</h4><p>{html.escape(body)}</p></div>""",
        unsafe_allow_html=True,
    )


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
st.markdown(
    """
    <div class="hero">
      <span class="badge">✨ AI untuk Video Clipper</span>
      <h1>ClipHook AI</h1>
      <p>Temukan momen viral & buat hook yang bikin penonton nggak scroll — dari link YouTube atau file kamu sendiri.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

default_key = ""
try:
    default_key = st.secrets.get("GEMINI_API_KEY", "")
except Exception:
    pass

with st.container(border=True):
    api_key = st.text_input(
        "🔑 Gemini API Key",
        value=default_key,
        type="password",
        placeholder="AIza...",
        help="Gratis di aistudio.google.com/apikey. Key tidak disimpan oleh aplikasi ini.",
    )

    source = st.radio(
        "Sumber video",
        ["🔗 Link YouTube", "📁 Upload file"],
        horizontal=True,
        label_visibility="collapsed",
    )

    yt_url, src_link, uploaded = "", "", None
    if source == "🔗 Link YouTube":
        yt_url = st.text_input("🔗 Link YouTube", placeholder="https://www.youtube.com/watch?v=...")
    else:
        uploaded = st.file_uploader(
            "Pilih file dari perangkat kamu",
            type=[e.strip(".") for e in list(TEXT_EXTS) + list(MEDIA_MIME.keys())],
            help="Transkrip: .txt .srt .vtt  |  Audio: .mp3 .wav .m4a .aac .ogg .flac  |  Video: .mp4 .mov .webm",
        )
        st.caption(
            "💡 Tercepat & paling hemat kuota: file transkrip (.txt/.srt/.vtt). "
            "Untuk audio/video, AI akan mendengarkan sendiri — lebih lama, dan **mp3** lebih ringan daripada video."
        )
        src_link = st.text_input(
            "Link YouTube asal (opsional)",
            placeholder="Isi agar timestamp di hasil bisa diklik",
        )

    with st.expander("⚙️ Pengaturan lanjutan"):
        model_name = st.text_input(
            "Model Gemini",
            value="gemini-3.8-flash",
            help="Jika muncul error 404 (model dihentikan), ganti dengan nama model Flash terbaru dari aistudio.google.com.",
        )
        extra_note = st.text_input(
            "Catatan untuk AI (opsional)",
            placeholder="Contoh: fokus momen lucu, target audiens anak muda, hindari topik politik",
        )
        manual_text = st.text_area(
            "Transkrip manual (opsional, jika pengambilan otomatis gagal)",
            height=140,
            placeholder="[00:12] teks...\n[00:25] teks...\n(salin dari fitur 'Show transcript' di YouTube)",
        )

    go = st.button("🚀 Analisis Video", type="primary")

if go:
    if not api_key:
        st.error("Masukkan Gemini API Key dulu.")
        st.stop()

    status = st.status("Memulai analisis...", expanded=True)

    def say(msg: str):
        status.write(msg)

    def fail(msg: str):
        status.update(label="Gagal", state="error", expanded=True)
        st.error(msg)
        st.stop()

    items, lang, media, vid = [], "?", None, None

    if source == "🔗 Link YouTube":
        vid = extract_video_id(yt_url) if yt_url else None
        if manual_text.strip():
            items = parse_manual_transcript(manual_text)
            lang = "manual"
        elif vid:
            with st.spinner("Mengambil transkrip YouTube..."):
                try:
                    items, lang = fetch_transcript(vid)
                except Exception as e:
                    msg = str(e)
                    if "blocked" in msg.lower() or "ip" in msg.lower():
                        fail(
                            "YouTube memblokir permintaan dari server ini (umum terjadi di cloud hosting). "
                            "Solusi: pakai tab 'Upload file' dengan transkrip, atau tempel transkrip manual di 'Pengaturan lanjutan'."
                        )
                    elif "disabled" in msg.lower() or "no transcript" in msg.lower():
                        fail("Video ini tidak punya transkrip/subtitle yang bisa diambil.")
                    else:
                        fail(f"Gagal mengambil transkrip: {msg[:300]}")
                    st.stop()
        else:
            fail("Link YouTube tidak valid. Contoh: https://www.youtube.com/watch?v=xxxxxxxxxxx")
            st.stop()
    else:
        if uploaded is None:
            fail("Pilih file dulu (transkrip, audio, atau video).")
            st.stop()
        vid = extract_video_id(src_link) if src_link else None
        ext = os.path.splitext(uploaded.name)[1].lower()
        if ext in TEXT_EXTS:
            raw_text = uploaded.getvalue().decode("utf-8", errors="ignore")
            items = parse_subtitle(raw_text) if ext in (".srt", ".vtt") else parse_manual_transcript(raw_text)
            lang = "file"
        else:
            try:
                media = upload_media(api_key, uploaded, say)
            except Exception as e:
                fail(f"Gagal mengunggah/memproses file: {str(e)[:300]}")

    transcript_text = ""
    if media is None:
        if not items:
            fail("Transkrip kosong atau formatnya tidak terbaca.")
            st.stop()
        transcript_text = build_transcript_text(items)
        st.caption(f"✅ Transkrip dibaca ({lang}) • {len(items)} segmen • {len(transcript_text):,} karakter")
    else:
        st.caption(f"✅ File media siap: {uploaded.name}")

    try:
        if True:
            say("🧠 AI sedang mencari momen viral & menulis hook...")
            try:
                result = call_gemini(api_key, model_name.strip(), transcript_text, extra_note, media, say)
            except json.JSONDecodeError:
                fail("AI mengembalikan format yang tidak valid. Coba klik Analisis sekali lagi.")
                st.stop()
            except Exception as e:
                msg = str(e)
                if "429" in msg or "quota" in msg.lower():
                    fail("Kuota API gratis habis / terlalu banyak permintaan. Tunggu sebentar lalu coba lagi.")
                elif "503" in msg or "UNAVAILABLE" in msg:
                    fail("Server Google sedang sibuk (bukan salah kamu). Tunggu 1-2 menit lalu klik Analisis lagi.")
                elif "timed out" in msg.lower() or "timeout" in msg.lower() or "504" in msg or "DEADLINE" in msg:
                    fail(
                        "AI terlalu lama merespons (lebih dari 4 menit). Biasanya karena file video terlalu panjang/berat. "
                        "Coba pakai audio (mp3), transkrip, atau potong videonya dulu."
                    )
                elif "404" in msg or "NOT_FOUND" in msg:
                    st.caption(f"Detail dari Google: {msg[:400]}")
                    fail(
                        "Model Gemini tidak ditemukan / sudah dihentikan. Buka 'Pengaturan lanjutan' dan ganti "
                        "nama model dengan Flash terbaru dari aistudio.google.com."
                    )
                elif "API key" in msg or "401" in msg or "403" in msg or "400" in msg:
                    fail("API Key tidak valid atau tidak punya akses ke model ini.")
                else:
                    fail(f"Terjadi kesalahan: {msg[:300]}")
                st.stop()
    finally:
        if media is not None:
            delete_media(api_key, media)
    status.update(label="Selesai! Hasil ada di bawah 👇", state="complete", expanded=False)

    st.session_state["result"] = result
    st.session_state["vid"] = vid

# ----------------------------------------------------------------------------
# Hasil
# ----------------------------------------------------------------------------
result = st.session_state.get("result")
vid = st.session_state.get("vid")

if result:
    if st.session_state.get("model_used"):
        st.caption(f"Model yang dipakai: {st.session_state['model_used']}")
    if result.get("ringkasan"):
        st.info(f"📝 **Ringkasan:** {result['ringkasan']}")

    all_text = []

    # 1. Momen viral
    st.markdown('<div class="section-title">🔥 5 Momen Paling Viral</div>', unsafe_allow_html=True)
    st.markdown('<div class="section-sub">Klik timestamp untuk langsung loncat ke momen di YouTube.</div>', unsafe_allow_html=True)
    for i, m in enumerate(result.get("momen_viral", [])[:5], 1):
        mulai, selesai = m.get("mulai", "00:00"), m.get("selesai", "00:00")
        time_label = f"⏱ {html.escape(str(mulai))} → {html.escape(str(selesai))}"
        if vid:
            link = f"https://youtu.be/{vid}?t={to_seconds(mulai)}"
            time_pill = f'<a class="pill" href="{link}" target="_blank">{time_label}</a>'
        else:
            time_pill = f'<span class="pill">{time_label}</span>'
        pills = (
            time_pill
            + f'<span class="pill score">🔥 Skor {html.escape(str(m.get("skor_viral", "-")))}/10</span>'
        )
        col_a, col_b = st.columns([6, 1.2])
        with col_a:
            render_card(f"#{i} {m.get('judul', '')}", m.get("alasan", ""), pills)
        with col_b:
            copy_text = f"{mulai} - {selesai} | {m.get('judul', '')}\nAlasan: {m.get('alasan', '')}"
            copy_button(copy_text)
        all_text.append(f"MOMEN {i}: {copy_text}")

    # 2. Hook text
    st.markdown('<div class="section-title">🅰️ 3 Opsi Hook Text</div>', unsafe_allow_html=True)
    st.markdown('<div class="section-sub">Teks besar untuk layar di 3 detik pertama.</div>', unsafe_allow_html=True)
    for i, h in enumerate(result.get("hook_text", [])[:3], 1):
        col_a, col_b = st.columns([6, 1.2])
        with col_a:
            st.markdown(
                f'<div class="card"><div class="meta"><span class="pill">Opsi {i}</span></div>'
                f'<div class="hooktext">{html.escape(h)}</div></div>',
                unsafe_allow_html=True,
            )
        with col_b:
            copy_button(h)
        all_text.append(f"HOOK TEXT {i}: {h}")

    # 3. Hook audio
    st.markdown('<div class="section-title">🎙️ Hook Audio</div>', unsafe_allow_html=True)
    st.markdown('<div class="section-sub">Narasi pembuka untuk direkam ulang oleh clipper.</div>', unsafe_allow_html=True)
    ha = result.get("hook_audio", "")
    if isinstance(ha, list):
        ha = " ".join(ha)
    col_a, col_b = st.columns([6, 1.2])
    with col_a:
        render_card("Narasi pembuka", ha, '<span class="pill">Voice-over</span>')
    with col_b:
        copy_button(ha)
    all_text.append(f"HOOK AUDIO: {ha}")

    # 4. Judul & caption
    st.markdown('<div class="section-title">✍️ Ide Judul & Caption TikTok/Reels</div>', unsafe_allow_html=True)
    st.markdown("**Judul**")
    for i, t in enumerate(result.get("judul", [])[:3], 1):
        col_a, col_b = st.columns([6, 1.2])
        with col_a:
            render_card(t, "", f'<span class="pill">Judul {i}</span>')
        with col_b:
            copy_button(t)
        all_text.append(f"JUDUL {i}: {t}")

    st.markdown("**Caption**")
    for i, c in enumerate(result.get("caption", [])[:3], 1):
        col_a, col_b = st.columns([6, 1.2])
        with col_a:
            render_card(f"Caption {i}", c, '<span class="pill">Caption</span>')
        with col_b:
            copy_button(c)
        all_text.append(f"CAPTION {i}: {c}")

    st.markdown("---")
    col_a, col_b = st.columns([3, 1.2])
    with col_a:
        st.caption("Salin semua hasil sekaligus:")
    with col_b:
        copy_button("\n\n".join(all_text), label="📋 Salin Semua")

    if vid:
        with st.expander("▶️ Pratinjau video"):
            st.video(f"https://www.youtube.com/watch?v={vid}")

st.markdown(
    "<div style='text-align:center;color:#6b7280;font-size:.8rem;margin-top:2rem'>"
    "ClipHook AI • Dibuat dengan Streamlit & Google Gemini</div>",
    unsafe_allow_html=True,
)
