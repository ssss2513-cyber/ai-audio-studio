"""Per-line acting cues, kept separate from spoken words and voice identity."""
import re

SUPPORTED_ENGINES = frozenset(('cosyvoice', 'cosyvoice3', 'gemini'))
INHERIT = '화자 스타일 사용'
INTENSITIES = ('약하게', '보통', '강하게')
EMOTIONS = {
    '기본': 'Speak naturally with a neutral, clear delivery.',
    '기쁨': 'Speak happily, with a warm smile and a buoyant, cheerful intonation.',
    '슬픔': 'Speak with sadness and restrained sorrow, using gentle, heartfelt phrasing.',
    '분노': 'Speak with anger and firm, tense emphasis.',
    '놀람': 'Speak with surprise and a brief sense of disbelief.',
    '두려움': 'Speak with fear and apprehension, maintaining intelligible words.',
    '울먹임': 'Speak in a tearful, choked-up tone, holding back tears while keeping every word clear.',
    '속삭임': 'Whisper confidentially with a soft, close voice and intelligible consonants.',
    '웃으며': 'Speak with an amused, smiling tone. Keep laughter in the delivery, without adding extra words.',
    '차분함': 'Speak calmly with relaxed, steady, reassuring phrasing.',
    '다정함': 'Speak tenderly and affectionately, with a gentle, caring tone.',
    '긴박함': 'Speak with urgency and tension, keeping the words distinct.',
    '단호함': 'Speak firmly and decisively, with controlled confidence.',
}
_ALIASES = {
    'neutral': '기본', '평상시': '기본', '중립': '기본',
    'happy': '기쁨', 'excited': '기쁨', '기쁘게': '기쁨', '신남': '기쁨',
    'sad': '슬픔', '슬프게': '슬픔',
    'angry': '분노', '화남': '분노', '화내며': '분노',
    'surprised': '놀람', '놀라며': '놀람',
    'scared': '두려움', 'fearful': '두려움', '공포': '두려움',
    'crying': '울먹임', 'tearful': '울먹임', '울먹이며': '울먹임',
    'whisper': '속삭임', 'whispering': '속삭임', '속삭이며': '속삭임',
    'laughing': '웃으며', '웃음': '웃으며',
    'calm': '차분함', '차분하게': '차분함',
    'warm': '다정함', '다정하게': '다정함',
    'urgent': '긴박함', '긴박하게': '긴박함',
    'confident': '단호함', '단호하게': '단호함',
}
_LEVEL_ALIASES = {'약': '약하게', 'mild': '약하게', '보통': '보통', 'normal': '보통',
                  '강': '강하게', 'strong': '강하게', **{key: key for key in INTENSITIES}}
_TAG = re.compile(r'\[([^\[\]\n]{1,48})\]')


def parse_marker(value):
    """Recognize only documented cues; ordinary bracketed prose is untouched."""
    pieces = re.split(r'\s*[,，:]\s*', str(value).strip(), maxsplit=1)
    name = pieces[0].strip().lower()
    emotion = name if name in EMOTIONS else _ALIASES.get(name, '')
    intensity = _LEVEL_ALIASES.get(pieces[1].strip().lower(), '') if len(pieces) > 1 else '보통'
    return (emotion, intensity) if emotion and intensity else ('', '보통')


def extract_emotion(text):
    """One cue per line. Extra/embedded cues get an explicit UI warning."""
    cues = []
    def remove(match):
        emotion, intensity = parse_marker(match.group(1))
        if not emotion:
            return match.group(0)
        cues.append((emotion, intensity, match.start()))
        return ' '
    cleaned = _TAG.sub(remove, str(text))
    if not cues:
        return cleaned, '', '보통', ''
    emotion, intensity, offset = cues[0]
    notes = []
    if str(text)[:offset].strip(' \t\"\'“”‘’'):
        notes.append('문장 중간의 감정도 이 대사 전체에 적용합니다. 중간에 감정을 바꾸려면 같은 화자로 줄을 나눠주세요.')
    if len(cues) > 1:
        notes.append('한 대사에는 첫 감정 표시만 적용합니다. 감정이 바뀌는 부분에서 줄을 나눠주세요.')
    return cleaned.strip(), emotion, intensity, ' '.join(notes)


def segment_cue(segment):
    return (getattr(segment, 'emotion', '') or '',
            getattr(segment, 'emotion_intensity', '보통') or '보통')


def acting_direction(emotion='', intensity='보통'):
    if not emotion:
        return ''
    if emotion not in EMOTIONS or intensity not in INTENSITIES:
        raise ValueError('대사별 감정과 강도를 다시 선택해주세요.')
    level = {'약하게': 'Use a subtle, understated emotional delivery.',
             '보통': 'Use a natural, clearly perceptible emotional delivery.',
             '강하게': 'Use a strong, expressive delivery with controlled articulation.'}[intensity]
    if emotion == '기본':
        level = ''
    return (EMOTIONS[emotion] + ' ' + level +
            ' Preserve the same speaker identity and gender. Speak Korean clearly. '
            'Do not add words, prolonged cries or exaggerated vocal shaking.').strip()


def require_support(engine, emotion):
    if emotion and engine not in SUPPORTED_ENGINES:
        raise ValueError('대사별 감정은 CosyVoice 2·3와 Gemini에서 지원합니다. '
                         '해당 화자의 엔진을 바꾸거나 감정을 ‘화자 스타일 사용’으로 돌려주세요.')


def tagged_line(segment, speaker=None):
    emotion, intensity = segment_cue(segment)
    tag = ('[' + emotion + (', ' + intensity if intensity != '보통' else '') + '] ') if emotion else ''
    return f'{speaker if speaker is not None else segment.speaker}: {tag}{segment.text}'
