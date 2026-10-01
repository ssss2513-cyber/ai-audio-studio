"""Stable reference identities for CosyVoice 3; no synthesis on import.

These are distinct VCTK speakers, not pitch-shifted copies or Gemini voices.
The English recordings condition Korean cross-language synthesis. UI labels
deliberately make no unverified claims about age or Korean pronunciation.
"""
MODEL_ID = 'FunAudioLLM/Fun-CosyVoice3-0.5B-2512'
MODEL_REVISION = '29e01c4e8d000f4bcd70751be16fa94bf3d85a18'
SOURCE_REVISION = '074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc'
SERVICE = 'voice-studio-cosyvoice3'
PROTOCOL = 'cosy3-jobs-v1'
BANK_REVISION = 'vctk20-20261001-v1'
VOICE_REPO = 'kyutai/tts-voices'
VOICE_REVISION = 'a0de156151266cf8eb27ac8f27312f7aff2ef7b8'
ATTRIBUTION = (
    'Reference recordings: CSTR VCTK Corpus, Christophe Veaux, Junichi Yamagishi '
    'and Kirsten MacDonald, University of Edinburgh. CC BY 4.0. '
    'https://doi.org/10.7488/ds/2645\n'
    'Reference distribution: Kyutai tts-voices, vctk/*_023.wav (mic1). '
    'https://huggingface.co/kyutai/tts-voices\n'
    'License: https://creativecommons.org/licenses/by/4.0/\n'
    'Output: new Korean synthetic speech conditioned on the reference; '
    'not an original recording or an endorsement by its speaker.\n'
)

_SPEAKERS = {
    '남성': ('p226', 'p227', 'p232', 'p237', 'p241', 'p243', 'p245', 'p246', 'p247', 'p251'),
    '여성': ('p225', 'p228', 'p229', 'p230', 'p233', 'p234', 'p236', 'p238', 'p239', 'p240'),
}
VOICEBANK = {
    f'{"M" if gender == "남성" else "F"}{number:02d}': {
        'name': f'{gender} {number:02d} · {speaker}',
        'gender': gender, 'source_speaker': speaker,
        'filename': f'vctk/{speaker}_023.wav', 'reference_language': 'en',
        'description': '서로 다른 실제 화자의 공개 녹음 · 영어 참고 음색으로 한국어 합성',
    }
    for gender, speakers in _SPEAKERS.items()
    for number, speaker in enumerate(speakers, 1)
}


def reference_url(voice):
    return f'https://huggingface.co/{VOICE_REPO}/resolve/{VOICE_REVISION}/{VOICEBANK[voice]["filename"]}'
