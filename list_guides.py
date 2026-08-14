from gallery.launch_guides import LAUNCH_GUIDES, ARTIFACT_ROUTES

print(f'Total guides: {len(LAUNCH_GUIDES)}\n')
for i, g in enumerate(LAUNCH_GUIDES, 1):
    print(f'{i:2}. {g["slug"]:<25} {g["name"]:<30} ({g["category"]})')

print(f'\n\nArtifact routes ({len(ARTIFACT_ROUTES)} total):\n')
for route in ARTIFACT_ROUTES:
    guides_str = ', '.join(route['guides'])
    print(f'  {route["value"]:<15} -> {guides_str}')
