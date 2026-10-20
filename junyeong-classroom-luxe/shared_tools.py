"""Versioned yadam downloads; package bytes use the existing member access checks."""
from pathlib import Path
import base64
import os
import tempfile

from fastapi import Request
from fastapi.responses import FileResponse
from shared_tool_release import RELEASES, ensure_release


PACKAGE_ROOT = Path(__file__).resolve().parent / 'shared-tools'
PACKAGES = (
    {
        'id': 'yadam-script-gpt',
        'asset': 'yadam-script-gpt-v2.3.zip',
        'filename': 'Yadam_Synopsis_Skill_v2.3.zip',
        'title': '대본 GPT 버전',
        'step': '01 · 시놉시스와 대본 만들기',
        'version': 'V2.3',
        'updated': '2026-10-15',
        'description': '소재나 레퍼런스 썸네일·제목에서 시놉시스 5안, 5막 20챕터, 약 35,000자 낭독 대본으로 이어지는 ChatGPT용 지침입니다. V2.3은 구조·사건·감정·후킹·문체를 나누어 설계하고, 도입·1~4막 끝·중간 후킹과 복선 회수, 구수한 낭독 문체를 보강했습니다. ‘옛날 옛적’ 첫마디와 유튜브 엔딩을 유지하며 30분·60분 분량도 선택할 수 있습니다.',
        'features': ['기본 90분·약 35,000자 · 30분·60분 선택 가능', '도입·1~4막 끝·중간 후킹과 복선 회수', '나레이션 약 80% · 대사 20% 이하 · 구수한 낭독체', '사진 없는 MD 14개 · 하위 폴더 없음'],
        'fileCount': 14,
    },
    {
        'id': 'yadam-script-titles',
        'asset': 'yadam-script-titles-v1.zip.b64',
        'filename': 'Yadam_Script_Titles_Skill.zip',
        'title': '썸네일 문구·제목 5세트 스킬',
        'step': '04 · 썸네일과 제목 만들기',
        'version': 'V1.0',
        'updated': '2026-10-14',
        'description': '완성된 야담 대본을 넣으면 썸네일 2줄 문구와 영상 제목을 5세트 만들고 최종 1안을 고르는 ChatGPT 스킬입니다.',
        'features': ['썸네일 2줄 문구·영상 제목 5세트', '대본 근거 확인·결말과 정체 숨김', '스킬 설정·참고 사진 5장 포함'],
        'fileCount': 8,
    },
    {
        'id': 'yadam-generator',
        'asset': 'yadam-generator-v13.9.1.zip',
        'filename': '야담_대본생성기_V13.9.1_몰입강화_전체파일.zip',
        'title': '야담 대본 생성기',
        'step': '01 · 대본 만들기',
        'version': 'V13.9.1 · 레퍼런스 흐름을 살린 각색',
        'updated': '2026-10-20',
        'description': '5막 20챕터와 별도 인트로·본편 합계 최소 35,000자를 기준으로 쓰는 야담 대본 생성기입니다. V13.9.1은 레퍼런스의 핵심 설정·큰 줄거리·감정선·반전 방향·결말의 의미를 살리고, 구체적인 사건·장면·대사·표현을 각색하도록 보완했습니다. 레퍼런스 각색에는 주요 요소 최소 4개 변경을 강제하지 않으며, V13.9의 작법·공감 행동·마지막 선택 점검을 유지합니다.',
        'features': ['레퍼런스의 설정·큰 흐름·감정선 유지 · 세부 사건·대사 각색', '각색에서는 최소 4개 변경 조건 해제 · 이름만 바꾸는 복제 방지', '5막 20챕터 · 35,000자 이상 · 60초 인트로 별도 · 본편 대사 최대 30%', '기존형·몰입 강화형 선택과 작법·공감 행동·마지막 선택 점검 유지'],
        'fileCount': 8,
        'sha256': 'b45376313fd47cd6bcf45fa111ad3773cf1c3b5b07c33f6fd757c597a2634ab4',
    },
    {
        'id': 'yadam-image-prompts',
        'asset': 'yadam-image-prompts-v2.7.6.zip',
        'filename': '야담_이미지제작_2.7.6.zip',
        'title': '이미지 프롬프트 생성기',
        'step': '02 · 이미지 구성하기',
        'version': '2.7.6',
        'updated': '2026-10-20',
        'description': '프롬프트 완성 후 GPT 이미지 생성을 묻는 2.7.6 버전입니다. 인물별 전신 2장·각도 4장을 먼저 만들고 모든 장면에 실제 에셋 이미지를 참조하도록 지시합니다.',
        'features': ['완성 후 GPT 이미지 생성 확인 질문', '인물별 전신 2장 + 각도 4장 · 총 6장', '장면마다 실제 에셋 참조 필수 · 누락 시 중단', '이미지 파일명 숫자_ 제거 · 원문·화풍 유지'],
        'fileCount': 70,
        'sha256': '648ee832595e8f4b669a1400e31e4e22c1abd6e8d42fb27756c6a8198aa2187b',
    },
    {
        'id': 'yadam-validator',
        'asset': 'yadam-validator-v11.22.zip',
        'filename': '야담_검증기_V11.22_몰입강화_지침포함.zip',
        'title': '야담 대본·인트로 검증기',
        'step': '03 · 완성본 검증하기',
        'version': 'V11.22 · V13.9 연동',
        'updated': '2026-10-20',
        'description': 'V13.9의 대본·인트로 완성도를 검토하는 검증기입니다. 작법 역할·초반 공감 행동·주요 장면 인과·마지막 선택을 원문 근거로 살피고, 요청한 부분만 별도 수정본으로 보완합니다. 주대본·인트로 분리와 화자 파일·분량 계산 기준도 함께 맞췄습니다.',
        'features': ['작법·초반 공감·장면 인과·마지막 선택 점검', '본편·인트로 분리 / 실제 분량·본편 화자 대응', '지침·검사 코드·감사 지도 포함 · 의미 검토 구분'],
        'fileCount': 8,
        'sha256': 'ede36fba61838b44057bdb2a6d7b48b0bc587d9b36f59a695d8e11de201b52c7',
    },
)


def package_path(package, data_root=None):
    if package['asset'] in RELEASES:
        if data_root is None:
            data_root = Path(os.getenv('CLASSROOM_DATA_DIR', str(PACKAGE_ROOT.parent / 'data')))
        return ensure_release(package['asset'], data_root)
    path = PACKAGE_ROOT / package['asset']
    if path.suffix != '.b64' or not path.is_file():
        return path
    decoded = Path(tempfile.gettempdir()) / 'maison-yadam-script-titles-v1.zip'
    payload = base64.b64decode(path.read_bytes(), validate=True)
    if not decoded.is_file() or decoded.read_bytes() != payload:
        decoded.write_bytes(payload)
    return decoded


def install_shared_tools(core):
    # Prepare the approved ZIP before this deployment can become healthy.
    # Subsequent requests use the private volume, without another Drive transfer.
    for package in PACKAGES:
        if package['asset'] in RELEASES:
            package_path(package, core.DATA)

    @core.app.get('/api/tools')
    def catalogue():
        items = []
        for package in PACKAGES:
            path = package_path(package, core.DATA)
            if not path.is_file():
                core.fail(503, '공유 파일을 준비하고 있습니다. 잠시 후 다시 확인해 주세요.')
            item = {key: value for key, value in package.items() if key != 'asset'}
            item['size'] = path.stat().st_size
            item['downloadUrl'] = '/api/tools/' + package['id'] + '/download'
            items.append(item)
        return {'items': items}

    @core.app.api_route('/api/tools/{ident}/download', methods=['GET', 'HEAD'])
    def download(ident: str, req: Request):
        core.require(req, watch=True)
        package = next((p for p in PACKAGES if p['id'] == ident), None)
        if package is None:
            core.fail(404, '등록된 공유 파일이 아닙니다.')
        path = package_path(package, core.DATA)
        if not path.is_file():
            core.fail(503, '공유 파일을 준비하고 있습니다. 잠시 후 다시 확인해 주세요.')
        return FileResponse(
            path,
            media_type='application/zip',
            filename=package['filename'],
            headers={'Cache-Control': 'private, no-store, max-age=0'},
        )





