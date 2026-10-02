"""Portable, non-executable job format shared by the site and Kaggle."""
FORMAT = 'voice-studio-cosy-kaggle-v1'
VERSION = '1.0.0'
SOURCE_REVISION = '074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc'
MODELS = {
    'cosyvoice': {
        'label': 'CosyVoice 2',
        'repo': 'FunAudioLLM/CosyVoice2-0.5B',
        'revision': 'eec1ae6c79877dbd9379285cf8789c9e0879293d',
        'yaml': 'cosyvoice2.yaml', 'tokenizer': 'speech_tokenizer_v2.onnx',
        'service': 'ai-voice-studio-cosyvoice', 'version': '2.9.18',
    },
    'cosyvoice3': {
        'label': 'CosyVoice 3',
        'repo': 'FunAudioLLM/Fun-CosyVoice3-0.5B-2512',
        'revision': '29e01c4e8d000f4bcd70751be16fa94bf3d85a18',
        'yaml': 'cosyvoice3.yaml', 'tokenizer': 'speech_tokenizer_v3.onnx',
        'service': 'voice-studio-cosyvoice3', 'version': '1.0.0',
    },
}
MAX_ITEMS = 4096
MAX_REFERENCE_BYTES = 10 * 1024 * 1024
MAX_REFERENCES_BYTES = 64 * 1024 * 1024
MAX_PLAN_BYTES = 16 * 1024 * 1024
