"""Text-only Gufo Qwen renderer at the installed immutable pin, no tools."""
import hashlib
import json
from pathlib import Path
from tokenizers import Tokenizer
import common as c

FILE = (c.ROOT/'runtime/qwen38/tokenizer/tokenizer.json') if (c.ROOT/'runtime/qwen38/tokenizer/tokenizer.json').exists() else Path('/home/funboy/StrixHaloQw38FN/runtime/qwen38/tokenizer/tokenizer.json')
SHA = '0997f410c57a1f4e53b09e4be8f4a172d90edd9564368fb0847030937229b9f3'
if hashlib.sha256(FILE.read_bytes()).hexdigest()!=SHA:
    raise RuntimeError('Tokenizer differs from the previously verified GGUF vocabulary/merges')
TOKENIZER = Tokenizer.from_file(str(FILE))
# Gufo v0.8.0 tokenizes client-authored vocabulary control spellings as text,
# while this small offline meter deliberately uses the older Python tokenizer.
# Refuse such rare ambiguous content rather than undercounting or treating it as
# server framing. The actual seven-case upgrade corpus is checked before freeze.
CONTENT_SPECIALS=tuple(x['content'] for x in json.loads(FILE.read_text()).get('added_tokens',[]) if x.get('special') and x.get('content'))

UNCENSORED_TEMPLATE_SHA256 = '924d3fcc64873b375d5909e8a664ba0aca97c9d6381f5ebda004d1b8a2fb4d64'
UNCENSORED_RENDERER_CONTRACT = 'qwen38-uncensored-native-template-c706-v1'

REASONING_INSTRUCTIONS = {
    'xhigh': ('Reasoning effort is set to xhigh. Please think carefully through the '
        'task, validate key assumptions, consider plausible alternatives, and '
        'prioritize correctness, consistency, and clarity in the final answer.'),
    'medium': '',
    'low': ('Reasoning effort is set to low. Keep your thinking brief and focused, '
        'moving directly to the conclusion without unnecessary elaboration.'),
}


def normalize_reasoning_effort(value):
    if value in (None, '', 'auto'):
        return 'xhigh'
    if value == 'high':
        return 'xhigh'
    if value not in REASONING_INSTRUCTIONS:
        raise ValueError('Qwen reasoning effort must be low, medium or xhigh')
    return value

def integer(value, name):
    if type(value) is not int or value<0:
        raise ValueError(name+' must be a nonnegative integer')
    return value

def render(messages, reasoning_effort_value="xhigh"):
    if not messages or messages[-1].get('role')!='user':
        raise ValueError('Conversation must end with a user turn')
    systems, turns = [], []
    for message in messages:
        if set(message)!={'role','content'} or not isinstance(message['content'],str):
            raise ValueError('Text-only role/content messages required; reasoning is not replayed')
        role, content = message['role'], message['content'].strip()
        if role not in ('system','user','assistant') or '\x00' in content:
            raise ValueError('Unsupported role or NUL content')
        if any(marker in content for marker in CONTENT_SPECIALS):
            raise ValueError('Literal tokenizer control spelling in content requires the native Gufo v0.8 renderer; request not dispatched')
        if role=='system':
            if turns:
                raise ValueError('System messages must precede turns')
            if content:
                systems.append(content)
        else:
            if role=='user' and content.startswith('<tool_response>'):
                raise ValueError('Tool-result framing is unsupported in this client')
            if turns and turns[-1][0]==role:
                raise ValueError('Adjacent equal roles are unsupported; combine explicitly')
            turns.append((role,content))
    effort=normalize_reasoning_effort(reasoning_effort_value)
    native_instruction=REASONING_INSTRUCTIONS[effort]
    text=''
    if systems or native_instruction:
        system_content=(native_instruction+('\n\n' if systems else '') if native_instruction else '')+'\n'.join(systems)
        text='<|im_start|>system\n'+system_content+'<|im_end|>\n'
    for role,content in turns:
        text+='<|im_start|>'+role+'\n'+content+'<|im_end|>\n'
    return text+'<|im_start|>assistant\n<think>\n'

def count(messages, output, capacity, *, reasoning_effort="xhigh"):
    integer(output,'max_output'); integer(capacity,'capacity')
    if output==0:
        raise ValueError('Output budget must be positive')
    effort=normalize_reasoning_effort(reasoning_effort)
    n=len(TOKENIZER.encode(render(messages,effort),add_special_tokens=False).ids)
    required=n+output+c.CONFIG['margin_tokens']
    if required>capacity:
        raise ValueError(f'Context rejected before dispatch: input{n}+output{output}+margin1024={required}, capacity{capacity}; no truncation')
    return {'input_tokens':n,'max_output':output,'margin':1024,'required':required,'capacity':capacity,
            'reasoning_requested':effort,'reasoning_effective':effort,'tokenizer_sha256':SHA,
            'renderer_contract':'qwen38-v080-native-reasoning-levels-v2'}

def render_uncensored(messages, reasoning_effort_value="xhigh"):
    """Render the candidate's embedded text-only template contract.

    The candidate vocabulary and merges are byte-identical to the resident
    Qwen tokenizer, but its chat template is a distinct pinned artifact.  The
    workbench supports the exact no-tools subset it sends in production:
    one optional leading system turn followed by alternating public user and
    assistant turns, ending in a user turn.  Prior hidden reasoning is never
    replayed because the HTTP payload fixes preserve_thinking=false.
    """
    if not messages or messages[-1].get('role') != 'user':
        raise ValueError('Conversation must end with a user turn')
    system = None
    turns = []
    for index, message in enumerate(messages):
        if set(message) != {'role', 'content'} or not isinstance(message['content'], str):
            raise ValueError('Text-only role/content messages required; reasoning is not replayed')
        role, content = message['role'], message['content'].strip()
        if role not in ('system', 'user', 'assistant') or '\x00' in content:
            raise ValueError('Unsupported role or NUL content')
        if any(marker in content for marker in CONTENT_SPECIALS):
            raise ValueError('Literal tokenizer control spelling in content requires the native Gufo v0.8 renderer; request not dispatched')
        if role == 'system':
            if index != 0 or system is not None:
                raise ValueError('Candidate system message must be the single first turn')
            system = content
            continue
        if role == 'user' and content.startswith('<tool_response>'):
            raise ValueError('Tool-result framing is unsupported in this client')
        if turns and turns[-1][0] == role:
            raise ValueError('Adjacent equal roles are unsupported; combine explicitly')
        turns.append((role, content))
    effort = normalize_reasoning_effort(reasoning_effort_value)
    native_instruction = REASONING_INSTRUCTIONS[effort]
    text = ''
    if system or native_instruction:
        system_content = (
            native_instruction + ('\n\n' if system else '')
            if native_instruction else ''
        ) + (system or '')
        text = '<|im_start|>system\n' + system_content + '<|im_end|>\n'
    for role, content in turns:
        text += '<|im_start|>' + role + '\n' + content + '<|im_end|>\n'
    return text + '<|im_start|>assistant\n<think>\n'


def count_uncensored(messages, output, capacity, *, reasoning_effort="xhigh"):
    integer(output, 'max_output'); integer(capacity, 'capacity')
    if output == 0:
        raise ValueError('Output budget must be positive')
    effort = normalize_reasoning_effort(reasoning_effort)
    prompt = render_uncensored(messages, effort)
    n = len(TOKENIZER.encode(prompt, add_special_tokens=False).ids)
    required = n + output + c.CONFIG['margin_tokens']
    if required > capacity:
        raise ValueError(
            f'Context rejected before dispatch: input{n}+output{output}+'
            f'margin1024={required}, capacity{capacity}; no truncation'
        )
    return {
        'input_tokens': n,
        'max_output': output,
        'margin': 1024,
        'required': required,
        'capacity': capacity,
        'reasoning_requested': effort,
        'reasoning_effective': effort,
        'tokenizer_sha256': SHA,
        'template_sha256': UNCENSORED_TEMPLATE_SHA256,
        'renderer_contract': UNCENSORED_RENDERER_CONTRACT,
        'rendered_prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
    }
