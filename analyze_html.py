import json, re

with open('/Users/gmx/interview/job_engine/bytedance_jobs.json') as f:
    data = json.load(f)

for entry in data['api_responses']:
    if entry['url'] == 'https://jobs.bytedance.com/':
        body = entry.get('body', '')
        if isinstance(body, str):
            print(f"HTML length: {len(body)}")
            print(f"First 3000 chars:")
            print(body[:3000])
            print(f"\n\n--- API paths found ---")
            apis = re.findall(r'/api/v[12]/[a-zA-Z_/]+', body)
            unique = sorted(set(apis))
            for a in unique:
                print(a)
        break
