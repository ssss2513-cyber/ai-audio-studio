"""Explicit model selection and resume; rendering never requests speech."""
import streamlit as st

from .gemini_keys import GeminiKeyInputError, parse_gemini_keys
from .generation_jobs import get_job, is_running


FLASH_25 = "gemini-2.5-flash-preview-tts"
RATE_LIMIT_URL = "https://aistudio.google.com/rate-limit?timeRange=last-28-days"


def _request_resume(work_dir, job_id):
    job = get_job(work_dir)
    if is_running(work_dir):
        st.session_state["_gemini_recovery_notice"] = "진행 중인 음성이 저장된 뒤 복구 버튼을 눌러주세요."
        return
    if not job or job.get("id") != job_id or not (job.get("engine_errors") or {}).get("gemini"):
        st.session_state["_gemini_recovery_notice"] = "작업 상태가 바뀌었습니다. 현재 진행 상황을 확인해주세요."
        return
    try:
        keys = parse_gemini_keys(st.session_state.get("gemini_api_key", ""))
    except GeminiKeyInputError as exc:
        st.session_state["_gemini_recovery_notice"] = str(exc)
        return
    if not keys:
        st.session_state["_gemini_recovery_notice"] = "왼쪽 Gemini API 키 입력칸에 사용할 키를 등록해주세요."
        return
    # This callback runs before the model/overwrite widgets are rendered.
    # A different model is used only after the user presses the labelled button.
    st.session_state["gemini_model"] = FLASH_25
    st.session_state["select_gemini_model"] = FLASH_25
    st.session_state["_reset_bulk_overwrite"] = True
    st.session_state["_gemini_recovery_request"] = {
        "job_id": job_id, "first": job["first"], "last": job["last"], "model": FLASH_25,
    }


def render_gemini_recovery(work_dir, job, active):
    notice = st.session_state.pop("_gemini_recovery_notice", "")
    if notice:
        st.warning(notice)
    if not job or not (job.get("engine_errors") or {}).get("gemini"):
        return
    progress = (job.get("engine_progress") or {}).get("gemini", {})
    remaining = max(0, progress.get("total", 0) - progress.get("done", 0))
    st.markdown("#### Gemini 미완료 대사 복구")
    st.write(f"Gemini 미완료 {remaining}개 · 완료된 음성은 재사용하고, 마지막에 대본 순서로 합칩니다.")
    st.caption("2.5 Flash는 예전 자동 재시도에 포함됐던 모델입니다. 성우·스타일 선택은 유지하지만, 모델 변경으로 음색·발성이 달라질 수 있습니다.")
    st.caption("2.5 Flash에도 해당 프로젝트의 이용 한도가 적용됩니다. 변경만으로 한도가 충분하다고 보장되지는 않습니다.")
    st.markdown(f"[Google AI Studio에서 모델별 사용 한도 확인]({RATE_LIMIT_URL})")
    if active:
        st.info("Cosy 등 진행 중인 생성이 끝나면 아래 복구 버튼이 활성화됩니다. 생성 중에는 새로고침하거나 캐시를 비우지 마세요.")
    st.button("▶ Gemini 2.5 Flash로 남은 대사 이어서 생성", key="gemini_resume_flash25",
              type="primary", use_container_width=True, disabled=active or remaining == 0,
              on_click=_request_resume, args=(work_dir, job["id"]))
    st.caption("현재 모델을 유지하려면 한도 초기화·상향 후 아래의 일반 ‘남은 대사 이어서 생성’ 버튼을 사용하세요.")
