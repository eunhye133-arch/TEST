import streamlit as st
import google.generativeai as genai
import requests
import io
import re
import zipfile
from PIL import Image
from urllib.parse import quote

# ── 상수 ──────────────────────────────────────────────────────────────────────
TEXT_MODEL = "gemini-2.0-flash"
CHARS_PER_SEC = 4.5

RATIO_SIZE = {
    "16:9": (1280, 720),
    "1:1":  (1024, 1024),
    "9:16": (720, 1280),
    "4:3":  (1024, 768),
    "3:4":  (768, 1024),
}

DEFAULT_STYLE = (
    "Upgraded stick-man 2D, thick black outline, pure white round face, "
    "single hard cel shading, flat matte colors; SCENE: {scene}, no text no letters"
)

DEFAULT_SYSTEM = """\
당신은 스틱맨 2D 애니메이션 전문 프롬프트 디렉터입니다.

스타일 규칙:
- 캐릭터: 흰 얼굴, 두꺼운 윤곽선, 셀 쉐이딩 1단, 스틱 팔다리, 무광 단색
- 배경: 저채도 평면 블록, 글자·숫자 절대 금지
- 금지: 3D, 포토리얼, 그라디언트, 텍스트, 말풍선

장면 묘사 규칙:
- 감정 → 눈썹/입 선으로 표현
- 동작 → 구체적 동사(leans, points, nods, gestures)
- 추상 개념 → 아이콘/도형으로 대체 (화살표, 차트, 기어, 빈 종이)

출력 형식 (영문 프롬프트만, 다른 설명 없이):
Upgraded stick-man 2D, thick black outline, pure white round face, single hard cel shading, flat matte colors; SCENE: [영문 장면 묘사], no text no letters
"""

# ── 페이지 설정 ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="스틱맨 이미지 생성기", page_icon="🎬", layout="wide")
st.title("🎬 스틱맨 이미지 생성기")
st.caption("대본 입력 → 장면 분할 → 이미지 자동 생성 (이미지 생성: Pollinations.ai 무료)")

# ── API Key (Secrets 우선, 없으면 사이드바 입력) ───────────────────────────────
_secret_key = st.secrets.get("GOOGLE_API_KEY", "")

# ── 사이드바 ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 설정")

    if _secret_key:
        api_key = _secret_key
        st.success("API Key가 Secrets에서 로드되었습니다.", icon="🔑")
    else:
        api_key = st.text_input(
            "Google AI Studio API Key (텍스트용)",
            type="password",
            placeholder="AIza...",
            help="https://aistudio.google.com 에서 발급 — 프롬프트 생성에만 사용",
        )

    st.divider()

    seconds_per_cut = st.slider(
        "컷당 초", min_value=1, max_value=10, value=4,
        help="한 이미지가 커버하는 대본 길이(초)",
    )

    aspect_ratio = st.selectbox(
        "이미지 비율",
        options=list(RATIO_SIZE.keys()),
        index=0,
    )

    st.divider()

    style_template = st.text_area(
        "스타일 템플릿 ({scene} 위치에 장면 묘사 삽입)",
        value=DEFAULT_STYLE,
        height=100,
    )

    system_prompt = st.text_area(
        "Gemini 시스템 프롬프트",
        value=DEFAULT_SYSTEM,
        height=180,
    )

    st.divider()
    st.caption(f"텍스트 모델: `{TEXT_MODEL}`")
    st.caption("이미지 생성: `Pollinations.ai` (무료)")

# ── 입력 영역 ──────────────────────────────────────────────────────────────────
st.subheader("📝 대본 입력")
script = st.text_area(
    "대본",
    height=160,
    placeholder="예: 부자들은 위기를 기회로 삼습니다. 주식 시장이 폭락할 때 오히려 매수 버튼을 누르죠.",
    label_visibility="collapsed",
)

run = st.button("🚀 이미지 생성", type="primary", use_container_width=True)

# ── 헬퍼 함수 ─────────────────────────────────────────────────────────────────

def validate() -> bool:
    if not api_key:
        st.error("Google API Key를 입력해주세요.")
        return False
    if not script.strip():
        st.error("대본을 입력해주세요.")
        return False
    return True


def build_text_client():
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(
        model_name=TEXT_MODEL,
        system_instruction=system_prompt,
    )


def split_script(text: str, sec: int) -> list[str]:
    chars = int(CHARS_PER_SEC * sec)
    sentences = re.split(r'(?<=[.!?。])\s+|(?<=다)\s+|(?<=죠)\s+|(?<=요)\s+', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    cuts, current = [], ""
    for s in sentences:
        if not current:
            current = s
        elif len(current) + len(s) + 1 <= chars:
            current += " " + s
        else:
            cuts.append(current)
            current = s
    if current:
        cuts.append(current)

    if len(cuts) >= 2 and len(cuts[-1]) < chars // 3:
        cuts[-2] += " " + cuts.pop()

    return cuts


def make_prompt(text_client, cut: str, template: str) -> str:
    user_msg = (
        f"Convert the following Korean script scene into an English-only image prompt.\n\n"
        f"Scene: {cut}\n\n"
        f"Rules:\n"
        f"- Output ONLY English text, absolutely NO Korean or other non-English characters\n"
        f"- No quotes, no code blocks, no explanations\n"
        f"- Use this exact format:\n"
        f"{template.replace('{scene}', '[describe the scene in English]')}"
    )
    resp = text_client.generate_content(user_msg)
    raw = resp.text.strip().strip('"').strip("'")
    raw = re.sub(r"```[a-z]*\n?", "", raw).strip("`").strip()
    # 한글 문자 완전 제거
    raw = re.sub(r'[가-힣ㄱ-ㅎㅏ-ㅣ]+', '', raw).strip()
    if "SCENE:" in raw:
        return raw
    return template.replace("{scene}", raw)


def generate_image(prompt: str, ratio: str) -> Image.Image | None:
    w, h = RATIO_SIZE[ratio]
    url = (
        f"https://image.pollinations.ai/prompt/{quote(prompt)}"
        f"?width={w}&height={h}&nologo=true&enhance=false&model=flux"
    )
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content))


def make_zip(cuts: list[str], images: list) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i, (_, img) in enumerate(zip(cuts, images)):
            if img is None:
                continue
            img_buf = io.BytesIO()
            img.save(img_buf, format="PNG")
            zf.writestr(f"cut_{i+1:02d}.png", img_buf.getvalue())
    return buf.getvalue()


# ── 실행 파이프라인 ────────────────────────────────────────────────────────────
if run:
    if not validate():
        st.stop()

    try:
        text_client = build_text_client()
    except Exception as e:
        st.error(f"텍스트 클라이언트 초기화 실패: {e}")
        st.stop()

    st.divider()

    # 1. 대본 분할
    with st.status("대본 분할 중...", expanded=False) as s1:
        cuts = split_script(script, seconds_per_cut)
        s1.update(label=f"대본 분할 완료 — {len(cuts)}컷 (컷당 {seconds_per_cut}초)", state="complete")

    with st.expander(f"✂️ 분할 결과 ({len(cuts)}컷)", expanded=True):
        for i, c in enumerate(cuts):
            st.markdown(f"**{i+1}.** {c}")

    st.divider()

    # 2. 이미지 프롬프트 생성
    prompts: list[str] = []
    with st.status("이미지 프롬프트 생성 중...", expanded=False) as s2:
        for i, cut in enumerate(cuts):
            s2.update(label=f"프롬프트 생성 중... ({i+1}/{len(cuts)})")
            try:
                prompts.append(make_prompt(text_client, cut, style_template))
            except Exception as e:
                st.warning(f"컷 {i+1} 프롬프트 생성 실패: {e}")
                prompts.append(style_template.replace("{scene}", cut[:80]))
        s2.update(label=f"프롬프트 생성 완료 — {len(prompts)}개", state="complete")

    with st.expander("💬 생성된 프롬프트", expanded=False):
        for i, p in enumerate(prompts):
            st.markdown(f"**컷 {i+1}:**")
            st.code(p, language="text")

    st.divider()

    # 3. 이미지 생성
    st.subheader("🖼️ 생성 결과")
    progress = st.progress(0, text="이미지 생성 준비 중...")
    images: list = []

    for i, (cut, prompt) in enumerate(zip(cuts, prompts)):
        progress.progress(i / len(prompts), text=f"이미지 생성 중... {i+1}/{len(prompts)}")
        try:
            img = generate_image(prompt, aspect_ratio)
            images.append(img)
        except Exception as e:
            st.warning(f"컷 {i+1} 이미지 생성 실패: {e}")
            images.append(None)

    progress.progress(1.0, text=f"완료! {sum(1 for img in images if img)}컷 생성됨")

    # 갤러리 (3열)
    COLS = 3
    for row_start in range(0, len(cuts), COLS):
        cols = st.columns(COLS)
        for col, idx in zip(cols, range(row_start, min(row_start + COLS, len(cuts)))):
            with col:
                st.markdown(f"**컷 {idx+1}**")
                st.caption(cuts[idx])
                img = images[idx]
                if img:
                    st.image(img, use_container_width=True)
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    st.download_button(
                        "💾 PNG 저장",
                        data=buf.getvalue(),
                        file_name=f"cut_{idx+1:02d}.png",
                        mime="image/png",
                        key=f"dl_{idx}",
                    )
                else:
                    st.info("생성 실패")

    st.divider()

    if any(images):
        st.download_button(
            "📦 전체 ZIP 다운로드",
            data=make_zip(cuts, images),
            file_name="stickman_cuts.zip",
            mime="application/zip",
            use_container_width=True,
        )
