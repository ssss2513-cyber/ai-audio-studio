"""Versioned yadam downloads; package bytes use the existing member access checks."""
from pathlib import Path
import base64
import tempfile

from fastapi import Request
from fastapi.responses import FileResponse


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
        'asset': 'yadam-generator-v13.8.1.zip',
        'filename': '야담_대본생성기_V13.8.1_몰입강화_전체파일.zip',
        'title': '야담 대본 생성기',
        'step': '01 · 대본 만들기',
        'version': 'V13.8.1 · 인트로 분리·도입 개선',
        'updated': '2026-10-20',
        'description': '5막 20챕터와 인트로·본편 합계 최소 35,000자를 기준으로 쓰는 야담 대본 생성기입니다. V13.8.1은 인트로 대사를 주대본에서 분리하고, 본편을 작품마다 다양한 옛이야기 표현으로 시작합니다. 새 작품은 기존형·몰입 강화형을 먼저 선택하며, 화자 파일과 검사 기준에도 같은 규칙을 적용합니다.',
        'features': ['주대본은 본편만 · 60초 인트로 별도 파일', '옛날 옛적·아주 오래전 등 작품별 시작 표현 변경', '기존형 / 몰입 강화형 최초 선택 · 임의 기본값 없음', '5막 20챕터·작법 1~2개·본편 대사 최대 30%'],
        'fileCount': 8,
    },
    {
        'id': 'yadam-image-prompts',
        'asset': 'yadam-image-prompts-v2.7.2-r1.zip',
        'filename': '야담_이미지제작_2.7.2_프로그램수정본_r1.zip',
        'title': '이미지 프롬프트 생성기',
        'step': '02 · 이미지 구성하기',
        'version': '2.7.2 수정본 r1',
        'updated': '2026-10-14',
        'description': '완성한 대본을 분석하고, 화풍과 인물을 유지한 장면별 이미지 프롬프트를 만드는 Claude 프로젝트용 도구입니다.',
        'features': ['화풍 선택·캐릭터 참조·장면별 영어 프롬프트', '대본 목록 앞 30자 표시·원문 전체 보존', '지침·코드·참고 이미지·설치 복구 파일 포함'],
        'fileCount': 64,
    },
    {
        'id': 'yadam-validator',
        'asset': 'yadam-validator-v11.21.zip',
        'filename': '야담_검증기_V11.21_몰입강화_지침포함.zip',
        'title': '야담 대본·인트로 검증기',
        'step': '03 · 완성본 검증하기',
        'version': 'V11.21 · 5막 20챕터',
        'updated': '2026-10-14',
        'description': '완성한 대본과 인트로를 살펴보고, 문제 있는 부분을 별도 수정본으로 보완하는 검증 도구입니다.',
        'features': ['5막 20챕터·역할·분량·결말 정서 점검', '지침·검사 코드·20챕터 감사 지도 포함', '60초 인트로·몰입도·대사·제목·썸네일 검토'],
        'fileCount': 8,
    },
)


def package_path(package):
    path = PACKAGE_ROOT / package['asset']
    if path.suffix != '.b64' or not path.is_file():
        return path
    decoded = Path(tempfile.gettempdir()) / 'maison-yadam-script-titles-v1.zip'
    payload = base64.b64decode(path.read_bytes(), validate=True)
    if not decoded.is_file() or decoded.read_bytes() != payload:
        decoded.write_bytes(payload)
    return decoded


def install_shared_tools(core):
    @core.app.get('/api/tools')
    def catalogue():
        items = []
        for package in PACKAGES:
            path = package_path(package)
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
        path = package_path(package)
        if not path.is_file():
            core.fail(503, '공유 파일을 준비하고 있습니다. 잠시 후 다시 확인해 주세요.')
        return FileResponse(
            path,
            media_type='application/zip',
            filename=package['filename'],
            headers={'Cache-Control': 'private, no-store, max-age=0'},
        )




