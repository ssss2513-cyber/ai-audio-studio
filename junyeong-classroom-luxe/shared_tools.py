"""Versioned yadam downloads; package bytes use the existing member access checks."""
from pathlib import Path
import base64
import tempfile

from fastapi import Request
from fastapi.responses import FileResponse


PACKAGE_ROOT = Path(__file__).resolve().parent / 'shared-tools'
PACKAGES = (
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
        'asset': 'yadam-generator-v13.7.zip',
        'filename': '야담_대본생성기_V13.7_몰입강화_전체파일.zip',
        'title': '야담 대본 생성기',
        'step': '01 · 대본 만들기',
        'version': 'V13.7 · 5막 20챕터',
        'updated': '2026-10-14',
        'description': '5막 20챕터와 합본 최소 35,000자를 고정하고, 10가지 작법 중 1~2개로 사건·반전·결말의 감정을 달리 만드는 Claude 프로젝트용 지침입니다.',
        'features': ['5막 20챕터·작법 1~2개·다양한 결말 정서', '모티프 640종·60초 인트로·대사 최대 30%', '기존형 / 자극 강화형 선택·최근 7일 중복 비교'],
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
        'asset': 'yadam-validator-v11.20.zip',
        'filename': '야담_검증기_V11.20_몰입강화_지침포함.zip',
        'title': '야담 대본·인트로 검증기',
        'step': '03 · 완성본 검증하기',
        'version': 'V11.20 · 5막 20챕터',
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

