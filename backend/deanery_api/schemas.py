import re
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, create_model


def field_type(spec, *, reading=False):
    name = spec['type']
    if name in ('integer','smallint','bigint'):
        return StrictInt
    if name == 'boolean': return StrictBool
    if name == 'uuid': return UUID
    if name in ('real','double precision'): return float
    if name.startswith('numeric'): return Decimal
    if name == 'date': return date
    if name.startswith('timestamp'): return datetime
    if name.startswith('time'): return time
    match = re.search(r'\((\d+)\)', name)
    return Annotated[StrictStr, Field(min_length=0 if reading else 1, max_length=int(match[1]) if match else 10000)]


def dto(name, spec, patch=False):
    fields = {}
    for field in spec['write_fields']:
        details = spec['fields'][field]
        kind = field_type(details)
        if details['nullable']: kind = kind | None
        default = None if patch or details['nullable'] or details.get('default') is not None else ...
        fields[field] = (kind, default)
    return create_model(name + ('Patch' if patch else 'Create'), __config__=ConfigDict(extra='forbid'), **fields)


def read_dto(name,spec):
    fields={}
    for field in spec['read_fields']:
        details=spec['fields'][field]
        kind=field_type(details,reading=True)
        if details['nullable']: kind=kind|None
        fields[field]=(kind,...)
    return create_model(name+'Read',__config__=ConfigDict(extra='forbid'),**fields)


def json_value(value):
    if isinstance(value, UUID): return str(value)
    if isinstance(value, Decimal): return str(value)
    if isinstance(value, (date,datetime,time)): return value.isoformat()
    if isinstance(value, dict): return {k:json_value(v) for k,v in value.items()}
    if isinstance(value, (list,tuple,set,frozenset)): return [json_value(v) for v in value]
    return value


class LiveHealth(BaseModel):
    status: Literal['ok']


class CoreHealth(BaseModel):
    status: Literal['ready']
    database_schema: str = Field(alias='schema')


class ComponentHealth(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status: Literal['ready','unavailable','unconfigured']
    reason: str | None = None
    engine_version: str | None = None
    signature_version: int | None = None
    signature_updated_at: datetime | None = None
    signature_age_seconds: int | None = None


class ReadinessHealth(BaseModel):
    status: Literal['ready','unavailable']
    database_schema: str = Field(alias='schema')
    components: dict[str,ComponentHealth]


class FieldRelation(BaseModel):
    table: str
    column: str
    on_delete: str


class ManifestField(BaseModel):
    type: str
    nullable: bool
    identity: bool | None = None
    default: str | None = None
    description: str | None = None
    relation: FieldRelation | None = None


class ResourceSpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind: Literal['table','view']
    database_schema: Literal['deanery'] = Field(alias='schema')
    key: list[str]
    fields: dict[str,ManifestField]
    read_fields: list[str]
    write_fields: list[str]
    policy: str
    read_roles: list[str]
    write_roles: list[str]
    operations: list[str]
    workflow_only: list[str]
    description: str | None
    operation_roles: dict[str,list[str]] | None = None
    private_read_fields: list[str] | None = None
    private_read_roles: list[str] | None = None
    agent_read_fields: list[str] | None = None


class ResourceManifest(BaseModel):
    version: int
    resources: dict[str,ResourceSpec]
