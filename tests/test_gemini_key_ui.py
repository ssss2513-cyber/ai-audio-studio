from streamlit.testing.v1 import AppTest
from streamlit.proto.TextInput_pb2 import TextInput


SOURCE = '''
import streamlit as st
from core.gemini_key_ui import render_key_inputs
value, keys = render_key_inputs()
st.session_state['resolved_keys_for_test'] = keys
'''


def test_existing_combined_three_keys_migrate_and_survive_rerun():
    app = AppTest.from_string(SOURCE)
    app.session_state['input_gemini_api_key'] = 'first-key,second-key,third-key'
    app.run()
    assert not app.exception
    assert [field.value for field in app.text_input] == ['first-key', 'second-key', 'third-key']
    assert '3개 인식됨' in app.info[0].value
    assert all(field.proto.type == TextInput.PASSWORD for field in app.text_input)
    app.run()
    assert app.session_state['resolved_keys_for_test'] == ['first-key', 'second-key', 'third-key']


def test_duplicates_are_not_misreported_as_additional_keys():
    app = AppTest.from_string(SOURCE).run()
    app.text_input(key='gemini_key_slot_1').set_value('first-key')
    app.text_input(key='gemini_key_slot_2').set_value('second-key')
    app.text_input(key='gemini_key_slot_3').set_value('first-key').run()
    assert not app.exception and '2개 인식됨' in app.info[0].value
    assert any('중복' in text.value for text in app.caption)
    app.text_input(key='gemini_key_slot_3').set_value('third-key').run()
    assert '3개 인식됨' in app.info[0].value


def test_more_than_three_existing_keys_are_not_discarded():
    app = AppTest.from_string(SOURCE)
    app.session_state['gemini_api_key'] = 'one-key,two-key,three-key,four-key'
    app.run()
    assert not app.exception and len(app.text_input) == 4
    assert len(app.session_state['resolved_keys_for_test']) == 4


def test_hiding_key_settings_does_not_erase_the_keys():
    source = '''
import streamlit as st
from core.gemini_key_ui import render_key_inputs
if st.checkbox('키 설정 표시', value=True, key='show'):
    render_key_inputs()
'''
    app = AppTest.from_string(source).run()
    for index, key in enumerate(('first-key', 'second-key', 'third-key'), 1):
        app.text_input(key=f'gemini_key_slot_{index}').set_value(key)
    app.run()
    app.checkbox(key='show').uncheck().run()
    assert not app.text_input
    app.checkbox(key='show').check().run()
    assert not app.exception
    assert [field.value for field in app.text_input] == ['first-key', 'second-key', 'third-key']
