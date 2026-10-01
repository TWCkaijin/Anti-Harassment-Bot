"""Decode an explicitly completed human blind review; never invent ratings."""

import argparse
import json
from pathlib import Path


def score(keys, review):
    if review.get('human_review') is not True:
        raise ValueError('Requires an exported human review, not generated model scores')
    if keys.get('schema_version') != 1 or review.get('schema_version') != 1:
        raise ValueError('Unsupported review schema')
    if not keys.get('dataset_sha256') or keys['dataset_sha256'] != review.get('dataset_sha256'):
        raise ValueError('The ratings do not belong to this exact blind report')
    mappings = keys.get('cases', {})
    if len(mappings) != 12:
        raise ValueError('The acceptance gate requires exactly 12 cases')
    rows = []
    all_fields_complete = True
    for case_id, mapping in mappings.items():
        if set(mapping) != {'A', 'B', 'C'} or set(mapping.values()) != {'main', 'before', 'candidate'}:
            raise ValueError('Invalid blind group mapping')
        main = next(k for k, v in mapping.items() if v == 'main')
        candidate = next(k for k, v in mapping.items() if v == 'candidate')
        pair = '/'.join(sorted([main, candidate]))
        rating = review.get('ratings', {}).get(case_id, {})
        all_fields_complete &= all(
            rating.get('groups', {}).get(label, {}).get(dimension) in {'1', '2', '3', '4', '5'}
            for label in ('A', 'B', 'C') for dimension in ('naturalness', 'directness', 'needs')
        ) and all(rating.get('pairs', {}).get(key) in {'better', 'equal', 'worse'}
                  for key in ('A/B', 'A/C', 'B/C'))
        choice = rating.get('pairs', {}).get(pair)
        if choice in {'better', 'worse'} and pair[0] != candidate:
            choice = {'better': 'worse', 'worse': 'better'}[choice]
        rows.append({'case_id': case_id, 'candidate_vs_main': choice or 'pending'})
    passed = sum(r['candidate_vs_main'] in {'better', 'equal'} for r in rows)
    complete = all_fields_complete and all(r['candidate_vs_main'] in {'better', 'equal', 'worse'} for r in rows)
    return {
        'reviewer': review.get('reviewer', ''), 'reviewed_at': review.get('reviewed_at'),
        'dataset_sha256': keys['dataset_sha256'],
        'cases': rows, 'not_worse_count': passed, 'required_count': 10,
        'human_gate': ('pass' if passed >= 10 else 'fail') if complete else 'pending',
        'legal_and_engineering_gate': 'requires_separate_verification',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('answer_key', type=Path)
    parser.add_argument('human_ratings', type=Path)
    args = parser.parse_args()
    print(json.dumps(score(json.loads(args.answer_key.read_text()),
                           json.loads(args.human_ratings.read_text())), ensure_ascii=False, indent=2))
