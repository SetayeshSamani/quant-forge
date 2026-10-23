"""Explicit backend-only dotenv settings. Process env > local file > defaults.

No shell evaluation, interpolation, automatic export, logs or import-time I/O.
Credentials and account IDs are excluded from repr and all public projections.
"""
from dataclasses import dataclass, field
import os
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]
KEYS=frozenset({"BOXAPI_API_KEY","BOXAPI_BASE_URL","BOXAPI_MODEL","WALLEX_API_KEY","WALLEX_SYMBOL",
    "WALLEX_ACCOUNT_TYPE","WALLEX_SUBACCOUNT_ID","TRADING_MODE","LIVE_TRADING_ENABLED","APP_ENV","DATA_DIR","BACKEND_PORT"})


class ConfigurationError(ValueError):
    pass


def read_dotenv(path):
    path=Path(path)
    if not path.exists():return {}
    if not path.is_file() or path.stat().st_size>65536:raise ConfigurationError("ENV_FILE_INVALID")
    try:lines=path.read_text(encoding="utf-8-sig").splitlines()
    except Exception:raise ConfigurationError("ENV_FILE_UNREADABLE") from None
    result={}
    for raw in lines:
        text=raw.strip()
        if not text or text.startswith('#'):continue
        if text.startswith('export '):text=text[7:].lstrip()
        key,sep,value=text.partition('=');key=key.strip();value=value.strip()
        if not sep or re.fullmatch(r"[A-Z_][A-Z0-9_]*",key) is None:raise ConfigurationError("ENV_SYNTAX_INVALID")
        if key in result:raise ConfigurationError("ENV_DUPLICATE_FIELD")
        if value.startswith(('"',"'")):
            quote=value[0];end=value.find(quote,1)
            if end<0 or value[end+1:].strip() and not value[end+1:].strip().startswith('#'):
                raise ConfigurationError("ENV_QUOTING_INVALID")
            value=value[1:end]  # Exact literal values: never execute/interpolate secrets or paths.
        else:
            value=re.split(r"\s+#",value,maxsplit=1)[0].rstrip()
        if any(ord(c)<32 for c in value):raise ConfigurationError("ENV_CONTROL_CHARACTER")
        result[key]=value
    return result


@dataclass(frozen=True)
class Settings:
    boxapi_api_key: str=field(repr=False)
    wallex_api_key: str=field(repr=False)
    wallex_subaccount_id: str=field(repr=False)
    boxapi_model: str
    boxapi_endpoint: str
    wallex_symbol: str
    wallex_account_type: str
    trading_mode: str
    live_trading_enabled: bool
    app_env: str
    data_dir: Path
    backend_port: int

    @property
    def assets(self):
        for quote in ('USDT','TMN'):
            if self.wallex_symbol.endswith(quote) and len(self.wallex_symbol)>len(quote):
                return self.wallex_symbol[:-len(quote)],quote
        raise ConfigurationError("SYMBOL_QUOTE_UNIT_UNSUPPORTED")


def load_settings(env_file=None, *, environ=None):
    values=read_dotenv(ROOT/'.env' if env_file is None else env_file)
    overrides=os.environ if environ is None else environ
    values.update({key:overrides[key] for key in KEYS if key in overrides})
    get=lambda key,default='':values.get(key,default)
    endpoint=get('BOXAPI_BASE_URL','https://ai.boxapi.ir/ai/v1').rstrip('/')
    if endpoint in ('https://ai.boxapi.ir','https://ai.boxapi.ir/ai/v1'):
        endpoint='https://ai.boxapi.ir/ai/v1/chat/completions'
    if endpoint!='https://ai.boxapi.ir/ai/v1/chat/completions':raise ConfigurationError("BOXAPI_ORIGIN_OR_ROUTE_NOT_ALLOWED")
    for key in ('BOXAPI_API_KEY','WALLEX_API_KEY'):
        secret=get(key)
        if secret and (len(secret)>4096 or any(not 33<=ord(c)<=126 for c in secret)):
            raise ConfigurationError("CREDENTIAL_FORMAT_INVALID")
    model=get('BOXAPI_MODEL','gpt-5.4-mini')
    if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,95}',model) is None:raise ConfigurationError("MODEL_FORMAT_INVALID")
    symbol=get('WALLEX_SYMBOL','BTCTMN').upper()
    if re.fullmatch(r'[A-Z0-9]{3,30}',symbol) is None:raise ConfigurationError("SYMBOL_FORMAT_INVALID")
    mode=get('TRADING_MODE','SHADOW').upper()
    live=get('LIVE_TRADING_ENABLED','false').lower()
    if mode not in ('SHADOW','REPLAY','LIVE') or live not in ('true','false','1','0'):
        raise ConfigurationError("TRADING_MODE_INVALID")
    account_type=get('WALLEX_ACCOUNT_TYPE','prop').lower()
    if account_type!='prop':raise ConfigurationError("DEDICATED_PROP_ACCOUNT_REQUIRED")
    subaccount=get('WALLEX_SUBACCOUNT_ID')
    if subaccount and re.fullmatch(r'[A-Za-z0-9_-]{1,200}',subaccount) is None:raise ConfigurationError("SUBACCOUNT_FORMAT_INVALID")
    app=get('APP_ENV','local').lower()
    if app not in ('local','development','test','production'):raise ConfigurationError("APP_ENV_INVALID")
    default_data=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local/share')))/'QuantForge/release-native-qa/data'
    data=Path(get('DATA_DIR') or default_data).expanduser()
    if not data.is_absolute():data=ROOT/data
    data=data.resolve()
    if data==ROOT or data.is_relative_to(ROOT) and not any(data.is_relative_to(ROOT/p) for p in ('data','.local')):
        raise ConfigurationError("DATA_DIR_MUST_USE_IGNORED_RUNTIME_DIRECTORY")
    try:port=int(get('BACKEND_PORT','8080'))
    except (ValueError,TypeError):raise ConfigurationError("BACKEND_PORT_INVALID") from None
    if not 1024<=port<=65535:raise ConfigurationError("BACKEND_PORT_INVALID")
    settings=Settings(get('BOXAPI_API_KEY'),get('WALLEX_API_KEY'),subaccount,model,endpoint,symbol,account_type,mode,live in ('true','1'),app,data,port)
    settings.assets
    return settings
