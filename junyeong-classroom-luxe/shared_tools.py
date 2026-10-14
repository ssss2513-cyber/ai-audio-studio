"""Versioned yadam downloads; package bytes use the existing member access checks."""
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse


PACKAGE_ROOT = Path(__file__).resolve().parent / 'shared-tools'
PACKAGES = (
    {
        'id': 'yadam-generator',
        'asset': 'yadam-generator-v13.7.zip',
        'filename': '야담_대본생성기_V13.7_몰입강화_전체파일.zip',
        'title': '야담 대본 생성기',
        'step': '01 · 대본 만들기',
        'version': 'V13.7',
        'updated': '2026-10-14',
        'description': '주제 선정부터 대본과 60초 인트로까지, Claude 프로젝트에 넣고 사용하는 제작 지침입니다.',
        'features': ['모티프 640종·이름·이야기 구조 자료', '60초 인트로·이미지·영상 프롬프트', '기존형 / 자극 강화형 선택·최근 7일 중복 비교'],
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
        'version': 'V11.20',
        'updated': '2026-10-14',
        'description': '완성한 대본과 인트로를 살펴보고, 문제 있는 부분을 별도 수정본으로 보완하는 검증 도구입니다.',
        'features': ['프로젝트 지침·검사 코드·지원 자료 포함', '몰입도·킬포인트·대사·60초 인트로 점검', '제목·썸네일·이미지·영상도 함께 검토 가능'],
        'fileCount': 8,
    },
)


def install_shared_tools(core):
    @core.app.get('/api/tools')
    def catalogue():
        items = []
        for package in PACKAGES:
            path = PACKAGE_ROOT / package['asset']
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
        path = PACKAGE_ROOT / package['asset']
        if not path.is_file():
            core.fail(503, '공유 파일을 준비하고 있습니다. 잠시 후 다시 확인해 주세요.')
        return FileResponse(
            path,
            media_type='application/zip',
            filename=package['filename'],
            headers={'Cache-Control': 'private, no-store, max-age=0'},
        )
