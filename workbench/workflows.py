"""Exact, fail-closed transformations of untrusted model output."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import common as c

def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()

def unique_pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: '+key)
        result[key]=value
    return result

def parse(text):
    text=text.strip()
    if text.startswith('```json\n') and text.endswith('\n```') and text.count('```')==2:
        text=text[8:-4]
    return json.loads(text,object_pairs_hook=unique_pairs,parse_constant=lambda value:(_ for _ in ()).throw(ValueError('Invalid JSON constant')))

DENY_PARTS={'.git','.env','.ssh','.aws','.azure','.config','node_modules','__pycache__','.venv','venv','state','credentials','secrets'}
EXTENSIONS={'.go','.py','.c','.h','.cpp','.hpp','.rs','.js','.jsx','.ts','.tsx','.mts','.cts','.mjs','.cjs','.css','.html','.md','.txt','.json','.toml','.yaml','.yml','.mod','.sum'}

def path_ok(path):
    if not isinstance(path,str) or len(path)>400 or '\\' in path or '\x00' in path:
        return False
    parts=PurePosixPath(path).parts
    if not parts or PurePosixPath(path).is_absolute() or any(x in ('.','..') or x.lower() in DENY_PARTS or x.lower().startswith('.env') for x in parts):
        return False
    name=parts[-1].lower()
    if any(x in name for x in ('id_rsa','id_ed25519','api-token','credential','secret')) or name.endswith(('.pem','.key')):
        return False
    return Path(path).suffix.lower() in EXTENSIONS

def editable(path):
    parts=tuple(x.lower() for x in PurePosixPath(path).parts)
    name=parts[-1] if parts else ''
    protected_dirs={'test','tests','__tests__','.github','scripts'}
    protected_names={'go.mod','go.sum','package.json','package-lock.json','pnpm-lock.yaml','pnpm-workspace.yaml',
        'requirements.txt','cargo.toml','cargo.lock','makefile','tsconfig.json','tsconfig.build.json','vitest.config.ts','jest.config.js'}
    node_test=bool(re.search(r'(?:^|\.)(?:test|spec)\.(?:[cm]?[jt]sx?)$',name)) or name.startswith('test_')
    return path_ok(path) and not (name.endswith('_test.go') or node_test or name in protected_names or
        any(x in protected_dirs for x in parts[:-1]) or name.startswith('tsconfig.') or name.endswith(('.yaml','.yml')))

def read_safe(root,path):
    if not path_ok(path):
        raise ValueError('Disallowed path: '+str(path))
    candidate=root/path
    if any(p.is_symlink() for p in [candidate,*candidate.parents] if p!=root.parent):
        raise ValueError('Symlink paths are disallowed')
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError('Path escapes snapshot')
    return candidate.read_text()

def materialize(base, destination, allowed, hashes, output):
    if destination.exists():
        raise ValueError('Existing candidate is never overwritten')
    data=parse(output)
    if set(data)!={'edits'} or not isinstance(data['edits'],list) or not 1<=len(data['edits'])<=32:
        raise ValueError('Expected one edits array with1 to32 exact edits')
    originals={path:read_safe(base,path) for path in hashes}
    if any(sha(text)!=hashes[path] for path,text in originals.items()):
        raise ValueError('Snapshot base hash changed')
    edits={}
    for edit in data['edits']:
        if not isinstance(edit,dict) or set(edit)!={'path','old_text','new_text'}:
            raise ValueError('Each edit must contain only path,old_text,new_text')
        path,old,new=edit['path'],edit['old_text'],edit['new_text']
        if path not in allowed or not editable(path) or path not in originals:
            raise ValueError('Edit is outside the fixed allowlist')
        if not isinstance(old,str) or not old or not isinstance(new,str) or '\x00' in new:
            raise ValueError('Nonempty exact old_text and text new_text required')
        positions=[m.start() for m in re.finditer('(?='+re.escape(old)+')',originals[path])]
        if len(positions)!=1:
            raise ValueError(f'old_text in {path} has{len(positions)} exact matches; expected1')
        start=positions[0]; end=start+len(old)
        if any(start<b and end>a for a,b,_ in edits.get(path,[])):
            raise ValueError('Overlapping edits rejected')
        edits.setdefault(path,[]).append((start,end,new))
    # All inputs are validated before any candidate tree exists. The original is unchanged.
    staged=destination.with_name(destination.name+'.staging')
    if staged.exists():
        raise ValueError('Interrupted staging tree requires reconciliation')
    for file in base.rglob('*'):
        if file.is_symlink():
            raise ValueError('Snapshot contains a symlink')
    shutil.copytree(base,staged)
    patch=''
    for path,changes in sorted(edits.items()):
        result=originals[path]
        for start,end,new in sorted(changes,reverse=True):
            result=result[:start]+new+result[end:]
        (staged/path).write_text(result)
        diff=subprocess.run(['git','diff','--no-index','--no-ext-diff','--no-textconv','--',str(base/path),str(staged/path)],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        if diff.returncode not in (0,1):
            raise ValueError('Git diff failed: '+diff.stderr[:200])
        # Git calculates all hunks. Only its generated path prefixes are normalized.
        chunk=diff.stdout.replace('a'+str(base)+'/', 'a/').replace('b'+str(staged)+'/', 'b/')
        patch+=chunk
    changed=sum(line.startswith(('+','-')) and not line.startswith(('+++','---')) for line in patch.splitlines())
    if changed>250:
        raise ValueError('250 changed-line task ceiling exceeded; candidate not committed')
    staged.rename(destination)
    return patch,{'edits':len(data['edits']),'changed_lines':changed,'base_hashes':hashes,'mechanism':'Exact original-text edits; Git-generated diff; no fuzzy repair'}

def word_count(text):
    return len(re.findall(r"\b[\w]+(?:['-][\w]+)*\b",text))

def writing_revision(output, parent):
    """Apply exact nonoverlapping edits to an immutable chapter, preserving quotes."""
    data=parse(output)
    if not isinstance(data,dict) or set(data)!={'base_sha256','edits'}:
        raise ValueError('Expected base_sha256 and chapter edits only')
    chapter=parent['chapter']
    if data['base_sha256']!=sha(chapter):
        raise ValueError('Chapter base hash mismatch')
    entries=data['edits']
    if not isinstance(entries,list) or not 1<=len(entries)<=32:
        raise ValueError('Expected1 to32 targeted chapter edits')
    changes=[]
    for edit in entries:
        if not isinstance(edit,dict) or set(edit)!={'old_text','new_text'}:
            raise ValueError('Chapter edit needs old_text and new_text only')
        old,new=edit['old_text'],edit['new_text']
        if not isinstance(old,str) or not old or not isinstance(new,str) or '\x00' in new:
            raise ValueError('Exact nonempty old_text and text new_text required')
        if old==chapter or len(old)>len(chapter)//2:
            raise ValueError('Use a full revision explicitly for whole-chapter rewrites')
        matches=[m.start() for m in re.finditer('(?='+re.escape(old)+')',chapter)]
        if len(matches)!=1:
            raise ValueError('Chapter old_text must have exactly one match')
        start=matches[0];end=start+len(old)
        if any(start<b and end>a for a,b,_ in changes):
            raise ValueError('Overlapping chapter edits')
        changes.append((start,end,new))
    if sum(b-a+len(new) for a,b,new in changes)>65536 or sum(b-a for a,b,_ in changes)>len(chapter)//2:
        raise ValueError('Targeted revision exceeds localized edit ceiling')
    revised=chapter
    for start,end,new in sorted(changes,reverse=True):
        revised=revised[:start]+new+revised[end:]
    if revised==chapter:
        raise ValueError('No chapter change')
    return {'chapter':revised,'quotes':parent['quotes']}, {'base_sha256':sha(chapter),
        'chapter_sha256':sha(revised),'edits':len(changes),'quotes_preserved':True,
        'mechanism':'Exact immutable-parent chapter edits, no fuzzy repair'}

def chapter_diff(root, before, after):
    """Git generates review hunks, never the model."""
    (root/'chapter-parent.md').write_text(before)
    (root/'chapter-source.md').write_text(after)
    result=subprocess.run(['git','diff','--no-index','--no-ext-diff','--no-textconv','--',
        str(root/'chapter-parent.md'),str(root/'chapter-source.md')],text=True,capture_output=True)
    if result.returncode not in (0,1):
        raise ValueError('Chapter diff failed')
    return result.stdout.replace('a'+str(root)+'/', 'a/').replace('b'+str(root)+'/', 'b/')

def resolve(document,quote):
    if not isinstance(quote,str) or not quote:
        return {'status':'MISSING'}
    occurrences=[m.start() for m in re.finditer('(?='+re.escape(quote)+')',document['text'])]
    if len(occurrences)!=1:
        return {'status':'MISSING' if not occurrences else 'AMBIGUOUS','matches':len(occurrences)}
    offset=occurrences[0]
    ends=[x for x in document.get('line_offsets',[]) if x[1]<=offset]
    line=ends[-1][0] if ends else document['text'][:offset].count('\n')+1
    return {'status':'RESOLVED','document':document['id'],'sha256':document['sha256'],
            'character_start':offset,'character_end':offset+len(quote),'source_line_start':line}

def writing(output, documents):
    data=parse(output)
    if set(data)!={'chapter','quotes'} or not isinstance(data['chapter'],str) or not isinstance(data['quotes'],list):
        raise ValueError('Expected chapter text and quotes array')
    chapter=data['chapter']; quotes=[]
    for item in data['quotes']:
        if not isinstance(item,dict) or set(item)!={'document','quote'}:
            raise ValueError('Each quote must declare document and exact quote text')
        doc=documents.get(item['document'])
        if doc is None or sha(doc['text'])!=doc['sha256']:
            result={'status':'UNKNOWN_DOCUMENT_OR_HASH_MISMATCH'}
        else:
            result=resolve(doc,item['quote'])
        quotes.append(dict(item,locator=result))
    words=word_count(chapter)
    headings=len(re.findall(r'(?m)^#{1,4}\s+\S',chapter))
    thirds=[0,0,0]
    for item in quotes:
        if item['locator']['status']=='RESOLVED':
            doc=documents[item['document']]
            thirds[min(2,3*item['locator']['character_start']//max(1,len(doc['text'])))]+=1
    distinct=len({(x['document'],x['quote']) for x in quotes})==len(quotes)
    good=1500<=words<=2500 and 3<=headings<=5 and len(quotes)==12 and distinct and all(x['locator']['status']=='RESOLVED' and 0<word_count(x['quote'])<=20 for x in quotes) and min(thirds)>=3
    rendered=chapter+'\n\n## Evidence appendix\n'+ '\n'.join(f'{i+1}. {x["quote"]} [{x["document"]}, '+(f'L{x["locator"]["source_line_start"]}' if x['locator']['status']=='RESOLVED' else x['locator']['status'])+']' for i,x in enumerate(quotes))+'\n'
    return rendered,{'status':'MECHANICAL_PASS_EDITORIAL_PENDING' if good else 'NEEDS_REVIEW','words':words,'headings':headings,
                     'quotes':quotes,'resolved_by_third':thirds,'distinct_quotes':distinct,'mechanical_pass':good,
                     'transform':'Original chapter unchanged; exact document-version quote lookup adds locators only'}
