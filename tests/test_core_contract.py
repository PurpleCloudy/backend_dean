"""Finite static contract checks; live PostgreSQL/HTTP gates are separate."""
from datetime import date
from decimal import Decimal
import pytest
from pydantic import ValidationError
from deanery_api.resources import manifest,models,key_dict
from deanery_api.schemas import json_value
from deanery_api.auth import hasher,verify_password
from deanery_api.workflows import command_models


def test_every_source_relation_has_explicit_contract():
    resources=manifest['resources']
    assert sum(x['kind']=='table' for x in resources.values())==56
    assert sum(x['kind']=='view' for x in resources.values())==27
    for name,spec in resources.items():
        assert spec['key'] and set(spec['key'])<=set(spec['read_fields']),name
        assert spec['policy'] and spec['read_roles'],name
        assert set(spec['write_fields'])<=set(spec['fields']),name
        assert 'password_hash' not in spec['read_fields'],name
        if spec['kind']=='view': assert spec['operations']==['read'] and spec['write_fields']==[]
    assert {'group_id','status_id','enrollment_date'}.isdisjoint(resources['student']['write_fields'])
    assert resources['grade']['operations']==['read','workflow']


def test_null_omitted_strict_types_and_composite_keys():
    patch=models['person']['update']
    assert patch.model_validate({'middle_name':None}).model_dump(exclude_unset=True)=={'middle_name':None}
    with pytest.raises(ValidationError): patch.model_validate({'last_name':None})
    with pytest.raises(ValidationError): models['classroom']['create'].model_validate({'building':'A','room_number':'1','capacity':True,'room_kind':'lecture'})
    with pytest.raises(ValidationError): patch.model_validate({'made_up':'x'})
    assert key_dict(manifest['resources']['student_contact'],{'student_id':'1','contact_person_id':'2'})=={'student_id':1,'contact_person_id':2}


def test_decimal_and_date_exact():
    assert json_value({'amount':Decimal('12345678.90'),'date':date(2026,10,6)})=={'amount':'12345678.90','date':'2026-10-06'}


async def test_argon2_real_hash_correct_wrong_and_legacy_placeholder():
    encoded=hasher.hash('A meaningful test password')
    assert await verify_password(encoded,'A meaningful test password')
    assert not await verify_password(encoded,'wrong')
    assert not await verify_password('hash_admin','A meaningful test password')


def test_six_workflows_and_grade_correction_are_typed():
    for name in ('enroll_student','expel_student','transfer_student','grant_academic_leave','return_from_leave','reinstate_student','request_grade_correction','decide_grade_correction'):
        assert name in command_models
        assert command_models[name].model_json_schema()['additionalProperties'] is False
    with pytest.raises(ValidationError):
        command_models['request_grade_correction'].model_validate({'sheet_number':'1','record_book':'1','new_points':45,'reason':'     '})
    assert 'correct_closed_grade' not in command_models


def test_read_strings_allow_source_empty_labels_but_writes_require_content():
    from pydantic import TypeAdapter,ValidationError
    from deanery_api.schemas import field_type
    import pytest
    source={'type':'character varying(120)'}
    assert TypeAdapter(field_type(source,reading=True)).validate_python('')==''
    with pytest.raises(ValidationError): TypeAdapter(field_type(source)).validate_python('')
