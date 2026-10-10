# React frontend integration patch

Current source patch for Lyaako/dean-agent-front at pinned commit e7d5f872ff072372d12d1890b7b90a8dd281bfc0 (react-frontend). Contains 64 frontend source, test, lockfile and documentation paths. SHA256: 1b90422a6d21601f7512fed8fc513a573291b9dcccc9b3b491b9ea588e6538f9.

Apply from the frontend checkout root using git apply. Runtime files, local environment credentials, uploads, node_modules and built assets are excluded from this curated patch.

Historical validation: 51/51 frontend checks, typecheck and production build passed before publication. The current patch was packaged without rerunning tests, build, clean-apply or other verification at the user's explicit request. Earlier clean-apply proofs apply to earlier patch hashes, not this new hash.

Live UI audit is partial. Some final responsive toolbar and PDF keyboard retakes remain pending; the personal-INN retake was blocked by automatic approval review before the sensitive edit. Real local-model tests recorded instruction-following, proposal and source/page-citation failures. This patch does not claim a fully completed audit or model readiness.

The previous 56-file patch is preserved locally in .nelson/missions/2026-10-10_184041_3a6cc6d8/iab-frontend-baseline/frontend-56files.patch. Current frontend source remains uncommitted in its separate local checkout; the backend repository publishes this portable patch.
