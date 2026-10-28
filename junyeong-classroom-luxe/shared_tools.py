"""Versioned yadam downloads; package bytes use the existing member access checks."""
from pathlib import Path
import base64
import hashlib
import os
import tempfile

from fastapi import Request
from fastapi.responses import FileResponse
from shared_tool_release import RELEASES, ensure_release
from shared_tool_type4 import build_type4_package
from shared_tool_image2710 import build_image2710_package


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
        'id': 'yadam-thumbnail-title',
        'asset': 'yadam-thumbnail-title-v1.zip.b64',
        'filename': 'Yadam_Thumbnail_Title_Skill.zip',
        'title': '야담 제목·썸네일 원스톱 제작 스킬',
        'step': '04 · 썸네일과 제목 만들기',
        'version': 'V1.0',
        'updated': '2026-10-28',
        'description': '대본을 분석하고 참고 화풍을 적용해 후킹 문구가 들어간 썸네일 이미지와 영상 제목을 제작하는 스킬입니다.',
        'features': ['대본 분석 · 후킹과 반전 중심 문구', '참고 화풍으로 새 장면 생성 · 색상과 글자 검수', '추천 제목 1개 · 대체 제목 2개 · 썸네일 이미지'],
        'fileCount': 1,
    },
    {
        'id': 'yadam-generator',
        'asset': 'yadam-generator-v13.11.1-type4.zip',
        'filename': '야담_대본생성기_V13.11.1_밤마실야담_4유형_전체파일.zip',
        'title': '야담 대본 생성기',
        'step': '01 · 대본 만들기',
        'version': 'V13.11.1 · 4번 밤마실야담 작법',
        'updated': '2026-10-26',
        'description': 'V13.11의 성인 로맨스·해학과 기존 세 유형을 그대로 유지하면서 4번 밤마실야담 작법을 추가했습니다. 비상식적인 행동 후킹, 첫 비밀의 중간 해명, 연쇄 궁금증과 생활 묘사, 과거 인연 및 초반 복선의 감정적 회수를 5막 20챕터에 적용합니다. 60초 인트로 분리·35,000자 이상·미취학 아동 제외 기준을 유지합니다.',
        'features': ['① 기존형 · ② 몰입 강화형 · ③ 매운맛 반전형 · ④ 밤마실야담 작법', 'V13.11 성인 로맨스·해학 규칙 및 기존 모티프·검증 유지', '상식 반전 후킹 · 연쇄 궁금증 · 생활 묘사 · 복선 회수', '5막 20챕터 · 35,000자 이상 · 별도 60초 인트로'],
        'fileCount': 10,
    },
    
    {'id': 'yadam-image-prompts',
     'asset': 'yadam-image-prompts-v2.7.10.zip',
     'filename': '야담_이미지제작_2.7.10.zip',
     'title': '이미지 프롬프트 생성기',
     'step': '02 · 이미지 구성하기',
     'version': '2.7.10',
     'updated': '2026-10-28',
     'description': '대본을 먼저 분석한 뒤 추천 화풍·원하는 화풍 직접 입력·참조 이미지 중 선택받습니다. 인물·배경·소품 합계 18개 에셋과 본편 프롬프트를 완성한 뒤 이미지 생성 동의를 받고, 에셋 생성·검수 후 실제 참조로 본편을 만듭니다. 에셋 ID별 파일명과 본편 원문 30자 저장명 대응표를 제공합니다.',
     'features': ['대본 분석 후 화풍 선택 · 자동 확정 금지',
                  '에셋 총 18장 · 인물 1명 = 전신 2컷+얼굴 4컷 통합 시트 1장',
                  '프롬프트 완성 후 생성 동의 · 에셋 완료 후 본편 생성',
                  '에셋 ID별 저장 · 본편 원문30자 숫자접두 없는 저장명 · 호출 ID 검사'],
     'fileCount': 79,
     'sha256': '351e56fd791274062fc0b15999dcb90ce3faff6cc70b5083c8498990679fb1f1'},
    {
        'id': 'yadam-validator',
        'asset': 'yadam-validator-v11.22.zip',
        'filename': '야담_검증기_V11.22_몰입강화_지침포함.zip',
        'title': '야담 대본·인트로 검증기',
        'step': '03 · 완성본 검증하기',
        'version': 'V11.22 · V13.9 연동',
        'updated': '2026-10-20',
        'description': 'V13.9의 대본·인트로 완성도를 검토하는 검증기입니다. 작법 역할·초반 공감·장면 인과·마지막 선택을 원문 근거로 살피고, 요청한 부분만 별도 수정본으로 보완합니다. 주대본·인트로 분리와 화자 파일·분량 계산 기준도 함께 맞췄습니다.',
        'features': ['작법·초반 공감·장면 인과·마지막 선택 점검', '본편·인트로 분리 / 실제 분량·본편 화자 대응', '지침·검사 코드·감사 지도 포함 · 의미 검토 구분'],
        'fileCount': 8,
        'sha256': 'ede36fba61838b44057bdb2a6d7b48b0bc587d9b36f59a695d8e11de201b52c7',
    },
)


def package_path(package, data_root=None):
    if package['id'] == 'yadam-image-prompts':
        if data_root is None:
            data_root = Path(os.getenv('CLASSROOM_DATA_DIR', str(PACKAGE_ROOT.parent / 'data')))
        return build_image2710_package(PACKAGE_ROOT, Path(data_root))
    if package['id'] == 'yadam-generator':
        if data_root is None:
            data_root = Path(os.getenv('CLASSROOM_DATA_DIR', str(PACKAGE_ROOT.parent / 'data')))
        return build_type4_package(PACKAGE_ROOT, Path(data_root))
    if package['asset'] in RELEASES:
        if data_root is None:
            data_root = Path(os.getenv('CLASSROOM_DATA_DIR', str(PACKAGE_ROOT.parent / 'data')))
        return ensure_release(package['asset'], data_root)
    path = PACKAGE_ROOT / package['asset']
    if path.suffix != '.b64' or not path.is_file():
        return path
    decoded = Path(tempfile.gettempdir()) / 'maison-yadam-thumbnail-title-v1.zip'
    payload = base64.b64decode(path.read_bytes(), validate=True)
    if not decoded.is_file() or decoded.read_bytes() != payload:
        decoded.write_bytes(payload)
    return decoded


def install_shared_tools(core):
    # Prepare the approved ZIP before this deployment can become healthy.
    # Subsequent requests use the private volume, without another Drive transfer.
    for package in PACKAGES:
        if package['asset'] in RELEASES or package['id'] in ('yadam-generator', 'yadam-image-prompts'):
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
            if package['id'] in ('yadam-generator', 'yadam-image-prompts'):
                item['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
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






