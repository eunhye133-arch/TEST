import streamlit as st
import google.generativeai as genai
from google import genai as genai_new
from google.genai import types
import io
import base64
import re
import zipfile
from PIL import Image

# ─────────────────────────────────────────────
# 상수 / 디폴트값
# ─────────────────────────────────────────────
DEFAULT_STYLE_GUIDE = """Upgraded stick-man 2D with thick black outline, pure white faces, single hard cel shading, thicker torso and neck, flat matte colors; SCENE: [행동 및 아이콘 묘사], no text or letters anywhere"""

DEFAULT_SYSTEM_PROMPT = """당신은 '2D 스틱맨 애니메이션 전문 프롬프트 디렉터'입니다.

스타일 가이드:
- 캐릭터: Pure-white round faces, single hard cel shading(턱 아래 1단 그림자), thick black outline, thicker torso and neck, stick limbs, flat matte colors.
- 배경: 저채도 평면 블록(Low saturation flat blocks), 글자 절대 금지.
- 네거티브: 3D, photoreal, gradient, soft light, text, letters, speech bubble 절대 금지.

장면 해석 규칙:
- 감정은 눈썹/입선으로, 동작은 명확한 동사(leans, points, nods, clasps, gestures)로 표현.
- 추상 개념 시각화:
  * 상승/하락 → 화살표 아이콘
  * 데이터/실적 → 차트 도형, 기어, 지도 핀
  * 계약/문서 → 빈 종이 아이콘
  * 모든 간판/화면/문서에 글자 대신 기호/도형만 사용.

출력 템플릿 (반드시 이 형식으로 시작):
Upgraded stick-man 2D with thick black outline, pure white faces, single hard cel shading, thicker torso and neck, flat matte colors; SCENE: [행동 및 아이콘 묘사 (영문) + no text/letters]
"""

IMAGE_MODEL = "imagen-3.0-generate-002"
TEXT_MODEL = "gemini-2.0-flash"

CHARS_PER_SEC = 4.5  # 한국어 1초당 평균 글자 수

# ─────────────────────────────────────────────
# 페이지 설정
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="🎬 스틱맨 이미지 생성기",
    page_icon="🎬",
    layout="wide",
)

st.title("🎬 스틱맨 이미지 생성기")
st.caption("대본을 입력하면 스틱맨 2D 스타일 이미지를 자동 생성합니다.")

# ─────────────────────────────────────────────
# 사이드바
# ─────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 설정")

    api_key = st.text_input(
        "Google AI Studio API Key",
        type="password",
        placeholder="AIza...",
        help="https://aistudio.google.com 에서 발급",
    )

    st.divider()

    seconds_per_cut = st.slider(
        "컷당 초 설정",
        min_value=1,
        max_value=10,
        value=4,
        step=1,
        help="한 이미지가 커버하는 대본 길이(초)",
    )

    st.divider()

    st.subheader("📐 이미지 형식 프롬프트 템플릿")
    format_template = st.text_area(
        "출력 프롬프트 형식 (커스텀 가능)",
        value=DEFAULT_STYLE_GUIDE,
        height=120,
        help="이미지 프롬프트의 시작 형식. [행동 및 아이콘 묘사] 부분이 장면별로 채워집니다.",
    )

    st.divider()
    st.caption("모델 정보")
    st.caption(f"텍스트: `{TEXT_MODEL}`")
    st.caption(f"이미지: `{IMAGE_MODEL}` (Imagen 3)")

# ─────────────────────────────────────────────
# 메인 영역
# ─────────────────────────────────────────────
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("📝 대본 입력")
    script = st.text_area(
        "대본을 입력하세요",
        height=200,
        placeholder="예시:\n부자들은 위기를 기회로 삼습니다. 주식 시장이 폭락할 때 오히려 매수 버튼을 누르죠.",
        label_visibility="collapsed",
    )

with col2:
    st.subheader("🎨 스타일 가이드 프롬프트")
    system_prompt = st.text_area(
        "Gemini 시스템 프롬프트 (스타일 지침)",
        value=DEFAULT_SYSTEM_PROMPT,
        height=200,
        label_visibility="collapsed",
        help="Gemini가 이미지 프롬프트를 생성할 때 따를 지침입니다.",
    )

st.divider()

run_button = st.button("🚀 이미지 생성 시작", type="primary", use_container_width=True)

# ─────────────────────────────────────────────
# 헬퍼 함수
# ─────────────────────────────────────────────

def validate_inputs():
    if not api_key:
        st.error("사이드바에서 Google AI Studio API Key를 입력해주세요.")
        return False
    if not script.strip():
        st.error("대본을 입력해주세요.")
        return False
    return True


def init_clients(key: str):
    """Google AI Studio 클라이언트 초기화"""
    # 텍스트 생성용 (google-generativeai)
    genai.configure(api_key=key)
    text_client = genai.GenerativeModel(
        model_name=TEXT_MODEL,
        system_instruction=system_prompt,
    )
    # 이미지 생성용 (google-genai 신규 SDK)
    image_client = genai_new.Client(api_key=key)
    return text_client, image_client


def analyze_script(text_client, script_text: str) -> str:
    """1단계: 대본 분석"""
    prompt = f"""아래 대본을 분석하여 다음 항목을 간결하게 답하세요:
1. 주제/핵심 메시지
2. 전체 예상 톤 (예: 진지, 유머, 교육적)
3. 주요 등장 개념/키워드
4. 예상 총 장면 수 (컷당 {seconds_per_cut}초 기준, 한국어 1초=약 {CHARS_PER_SEC}글자)

대본:
\"\"\"
{script_text}
\"\"\"
"""
    response = text_client.generate_content(prompt)
    return response.text


def segment_script(script_text: str, sec: int) -> list[str]:
    """2단계: 초단위 분할 (규칙 기반 + Gemini 보정)"""
    chars_per_cut = int(CHARS_PER_SEC * sec)
    # 공백 제거 후 글자 수 계산용
    clean = script_text.strip()

    # 문장 경계 우선 분리
    sentences = re.split(r'(?<=[.!?。])\s*|\s*(?=[가-힣])', clean)
    sentences = [s.strip() for s in sentences if s.strip()]

    cuts = []
    current = ""
    for sentence in sentences:
        if not current:
            current = sentence
        elif len(current) + len(sentence) <= chars_per_cut:
            current += " " + sentence
        else:
            cuts.append(current.strip())
            current = sentence
    if current.strip():
        cuts.append(current.strip())

    # 너무 짧은 컷은 앞 컷에 병합
    merged = []
    for cut in cuts:
        if merged and len(cut) < chars_per_cut // 3:
            merged[-1] += " " + cut
        else:
            merged.append(cut)

    return merged


def generate_image_prompts(text_client, cuts: list[str], template: str) -> list[str]:
    """3단계: 각 컷을 이미지 프롬프트로 변환"""
    prompts = []
    for i, cut in enumerate(cuts):
        prompt = f"""아래 대본 컷을 스틱맨 2D 스타일 이미지 프롬프트로 변환하세요.

반드시 다음 형식으로만 출력하세요 (다른 설명 없이):
{template.replace('[행동 및 아이콘 묘사]', '[SCENE DESCRIPTION IN ENGLISH]')}

대본 컷 {i+1}: {cut}

규칙:
- SCENE 부분에 영문으로 구체적인 행동과 아이콘 묘사 작성
- no text, no letters 강조 포함
- 순수 프롬프트 텍스트만 출력 (따옴표, 코드블록 없이)
"""
        response = text_client.generate_content(prompt)
        raw = response.text.strip().strip('"').strip("'").strip()
        # 코드블록 제거
        raw = re.sub(r"```[a-z]*\n?", "", raw).strip("`").strip()
        prompts.append(raw)
    return prompts


def generate_image(image_client, prompt: str) -> Image.Image | None:
    """4단계: Imagen 3으로 이미지 생성"""
    response = image_client.models.generate_images(
        model=IMAGE_MODEL,
        prompt=prompt,
        config=types.GenerateImagesConfig(
            number_of_images=1,
            aspect_ratio="16:9",
            safety_filter_level="block_few",
            person_generation="allow_adult",
        ),
    )
    if response.generated_images:
        img_bytes = response.generated_images[0].image.image_bytes
        return Image.open(io.BytesIO(img_bytes))
    return None


def images_to_zip(cuts: list[str], images: list[Image.Image | None]) -> bytes:
    """생성된 이미지를 ZIP으로 묶기"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i, (cut, img) in enumerate(zip(cuts, images)):
            if img is None:
                continue
            img_buf = io.BytesIO()
            img.save(img_buf, format="PNG")
            zf.writestr(f"cut_{i+1:02d}.png", img_buf.getvalue())
    return buf.getvalue()


# ─────────────────────────────────────────────
# 실행 파이프라인
# ─────────────────────────────────────────────
if run_button:
    if not validate_inputs():
        st.stop()

    try:
        text_client, image_client = init_clients(api_key)
    except Exception as e:
        st.error(f"클라이언트 초기화 실패: {e}")
        st.stop()

    result_area = st.container()

    with result_area:
        # ── 1단계: 대본 분석 ──────────────────────
        with st.status("1단계: 대본 분석 중...", expanded=True) as status1:
            try:
                analysis = analyze_script(text_client, script)
                status1.update(label="1단계: 대본 분석 완료", state="complete", expanded=False)
            except Exception as e:
                status1.update(label=f"1단계 실패: {e}", state="error")
                st.stop()

        with st.expander("📊 대본 분석 결과", expanded=False):
            st.markdown(analysis)

        st.divider()

        # ── 2단계: 초단위 분할 ────────────────────
        with st.status("2단계: 초단위 분할 중...", expanded=True) as status2:
            try:
                cuts = segment_script(script, seconds_per_cut)
                status2.update(label=f"2단계: 분할 완료 ({len(cuts)}컷)", state="complete", expanded=False)
            except Exception as e:
                status2.update(label=f"2단계 실패: {e}", state="error")
                st.stop()

        with st.expander(f"✂️ 분할 결과 ({len(cuts)}컷, 컷당 {seconds_per_cut}초)", expanded=True):
            for i, cut in enumerate(cuts):
                st.markdown(f"**{i+1}.** {cut}")

        st.divider()

        # ── 3단계: 이미지 프롬프트 생성 ──────────
        with st.status("3단계: 이미지 프롬프트 생성 중...", expanded=True) as status3:
            try:
                image_prompts = generate_image_prompts(text_client, cuts, format_template)
                status3.update(label=f"3단계: 프롬프트 생성 완료 ({len(image_prompts)}개)", state="complete", expanded=False)
            except Exception as e:
                status3.update(label=f"3단계 실패: {e}", state="error")
                st.stop()

        with st.expander("💬 생성된 이미지 프롬프트", expanded=False):
            for i, p in enumerate(image_prompts):
                st.markdown(f"**컷 {i+1}:**")
                st.code(p, language="text")

        st.divider()

        # ── 4단계: 이미지 생성 ────────────────────
        st.subheader("🖼️ 이미지 생성 결과")
        progress_bar = st.progress(0, text="이미지 생성 준비 중...")
        generated_images = []

        for i, (cut, prompt) in enumerate(zip(cuts, image_prompts)):
            progress_bar.progress(
                (i) / len(image_prompts),
                text=f"이미지 생성 중... ({i+1}/{len(image_prompts)}) - 컷 {i+1}",
            )
            try:
                img = generate_image(image_client, prompt)
                generated_images.append(img)
            except Exception as e:
                st.warning(f"컷 {i+1} 이미지 생성 실패: {e}")
                generated_images.append(None)

        progress_bar.progress(1.0, text=f"완료! 총 {len(generated_images)}컷 생성됨")

        # 결과 갤러리 (3열)
        cols_per_row = 3
        rows = [generated_images[i:i+cols_per_row] for i in range(0, len(generated_images), cols_per_row)]
        cut_rows = [cuts[i:i+cols_per_row] for i in range(0, len(cuts), cols_per_row)]
        prompt_rows = [image_prompts[i:i+cols_per_row] for i in range(0, len(image_prompts), cols_per_row)]

        cut_idx = 0
        for row_imgs, row_cuts, row_prompts in zip(rows, cut_rows, prompt_rows):
            cols = st.columns(cols_per_row)
            for col, img, cut_text, ptext in zip(cols, row_imgs, row_cuts, row_prompts):
                cut_idx += 1
                with col:
                    st.markdown(f"**컷 {cut_idx}**")
                    st.caption(cut_text)
                    if img:
                        st.image(img, use_container_width=True)
                        # 개별 다운로드
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        st.download_button(
                            label="PNG 저장",
                            data=buf.getvalue(),
                            file_name=f"cut_{cut_idx:02d}.png",
                            mime="image/png",
                            key=f"dl_{cut_idx}",
                        )
                    else:
                        st.info("생성 실패")

        st.divider()

        # 전체 ZIP 다운로드
        valid_imgs = [img for img in generated_images if img]
        if valid_imgs:
            zip_data = images_to_zip(cuts, generated_images)
            st.download_button(
                label="모든 이미지 ZIP으로 다운로드",
                data=zip_data,
                file_name="stickman_images.zip",
                mime="application/zip",
                use_container_width=True,
            )
