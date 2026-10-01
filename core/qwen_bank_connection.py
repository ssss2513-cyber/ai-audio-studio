"""Fresh, stateless connection checks for the only supported Qwen voice bank.

Keep connection identity separate from long-running synthesis clients so a UI
release does not require reloading worker pools or interrupting existing audio.
"""
import re
import requests

from .personal_colab import connection_url
from qwen_voicebank_catalog import BASE_MODEL, BANK_REVISION

MODEL_ID = BASE_MODEL
SERVICE = "voice-studio-qwen-voicebank"
CONNECTION_REPORT_VERSION = "qwen-bank-status-v2"
CONNECTION_UI_LABEL = "20종 연결 화면 v2.9.40"


def api_base(value):
    url = connection_url(value, "qwen-bank")
    if not url:
        raise ValueError("20종 전용 코랩 4번 셀의 프로그램 연결 주소 전체를 넣어주세요.")
    return url


def _json_response(response):
    with response:
        if response.status_code == 404:
            raise RuntimeError("Qwen 코랩 주소 또는 작업을 찾지 못했습니다. 코랩 4번 셀에서 나온 주소를 확인해주세요.")
        if not response.ok:
            raise RuntimeError(f"Qwen 코랩 요청 실패 (HTTP {response.status_code}). 코랩 화면의 상태를 확인해주세요.")
        if len(response.content) > 128 * 1024:
            raise RuntimeError("Qwen 서버의 상태 응답이 너무 큽니다.")
        try:
            data = response.json()
        except ValueError:
            raise RuntimeError("Qwen 코랩이 정상적인 상태 정보를 보내지 않았습니다.") from None
        if not isinstance(data, dict):
            raise RuntimeError("Qwen 코랩 상태 형식을 확인할 수 없습니다.")
        return data


def connection_report(value):
    """Identify the responding server without treating every mismatch as 9 voices."""
    details = {}
    try:
        with requests.Session() as session:
            data = _json_response(session.get(api_base(value) + "/health", timeout=(10, 20), allow_redirects=False))
        # Include only identity fields, never the submitted URL, access token or
        # the full server response. Streamlit renders these values as plain text.
        for key, label in (("service", "서버 종류"), ("model", "모델"),
                           ("version", "서버 버전"), ("protocol", "연결 방식 버전"),
                           ("bank_revision", "목소리 목록 버전"), ("ready", "준비 완료")):
            raw = data.get(key)
            if raw is None:
                details[label] = "응답에 없음"
            elif type(raw) not in (str, int, float, bool):
                details[label] = "응답 형식 확인 필요"
            elif re.search(r"https?://|trycloudflare|/v1/", str(raw), re.I):
                details[label] = "주소가 포함된 값은 표시하지 않습니다"
            else:
                details[label] = " ".join(str(raw).split())[:160]
        if data.get("service") == "voice-studio-qwen-customvoice":
            return False, (
                "현재 주소에서는 지원 종료된 Qwen CustomVoice 서버가 응답했습니다. "
                "기존 서버 주소는 20종 서버 주소로 바뀌지 않습니다. "
                "위 ‘20종 전용 코랩 v1.0.2 열기’에서 1 → 2 → 4번을 실행하고 새 주소를 넣어주세요."
            ), details
        if data.get("service") != SERVICE:
            return False, (
                "서버는 응답했지만 기본 목소리 20종 서버임을 확인하지 못했습니다. "
                "아래 ‘연결 확인 결과’를 확인해주세요."
            ), details
        if data.get("model") != MODEL_ID:
            return False, (
                "20종 서버는 확인됐지만 연결된 모델 정보가 사이트와 다릅니다. "
                "아래 ‘연결 확인 결과’의 모델을 확인해주세요. 주소 입력칸을 바꿀 문제는 아닙니다."
            ), details
        if data.get("protocol") != 1:
            return False, (
                "20종 서버의 연결 방식 버전이 사이트와 맞지 않거나 응답에 빠져 있습니다. "
                "아래 ‘연결 확인 결과’를 확인해주세요."
            ), details
        if data.get("bank_revision") != BANK_REVISION:
            details["사이트 목소리 목록 버전"] = BANK_REVISION
            return False, (
                "20종 서버의 목소리 목록 버전이 사이트와 다르거나 응답에 빠져 있습니다. "
                "아래 ‘연결 확인 결과’에 두 버전을 표시했습니다. 목소리 목록이 일치하는 20종 전용 코랩이 필요합니다."
            ), details
        if data.get("ready") is not True:
            return False, "20종 서버는 확인됐으며 아직 준비 중입니다. 코랩 2번 셀에서 준비 완료를 기다려주세요.", details
        return True, (f"기본 목소리 20종 서버 연결 완료 · {data.get('gpu', 'GPU')} · "
                      f"GPU 배치 최대 {data.get('batch_size', 1)}개 · 첫 사용 목소리는 생성 때 준비"), details
    except (ValueError, RuntimeError) as exc:
        return False, str(exc), details
    except requests.RequestException:
        return False, "Qwen 코랩에 연결하지 못했습니다. 코랩 실행 상태와 최신 주소를 확인해주세요.", details


def connection_status(value):
    ok, message, _ = connection_report(value)
    return ok, message


