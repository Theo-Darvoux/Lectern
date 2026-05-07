import yara
import os

rules = yara.compile(filepaths={
    'doc_adv': 'api/yara_rules/document_advanced.yar',
    'doc_exp': 'api/yara_rules/document_exploits.yar',
    'eicar': 'api/yara_rules/eicar.yar',
    'mal_com': 'api/yara_rules/malware_common.yar'
})

folder = '/home/psders/Downloads/CorrigesNaga'
for f in sorted(os.listdir(folder)):
    if f.endswith('.pdf'):
        path = os.path.join(folder, f)
        matches = rules.match(path)
        if matches:
            print(f"{f}: {matches}")
