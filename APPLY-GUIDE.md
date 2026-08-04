# 적용 순서

## 1. GitHub 저장소 교체

이 폴더의 파일을 `jhj2525/semicon-battery-brief` 저장소 루트에 업로드합니다.
기존 파일과 이름이 같으면 덮어씁니다. `data/news.json`은 초기화본이므로 기존 기사,
아카이브, 수동 기사, 즐겨찾기가 모두 비워집니다. 기존에 삭제한 기사 ID 3개는 유지됩니다.

업로드 직후 사이트는 비어 있습니다. 다음 정기 실행에서 첫 자동 묶음이 생성됩니다.
입력값 없이 `Run workflow`를 눌러도 자동 묶음은 생성되거나 교체되지 않습니다.

## 2. Cloudflare Worker 교체

Cloudflare의 `process-brief-manual-add` Worker 편집 화면에서 기존 코드를
`cloudflare-worker.js` 내용으로 전부 교체하고 배포합니다.

## 3. Worker Secret 추가

Worker `Settings → Variables and Secrets`에서 다음 Secret을 추가합니다.

- 이름: `ADMIN_TOKEN_SECRET`
- 값: 비밀번호 관리자가 생성한 40자 이상의 임의 문자열
- 유형: Secret

기존 `ADMIN_PASSWORD`, `GITHUB_TOKEN`은 유지합니다. `ADMIN_TOKEN_SECRET`은
관리 비밀번호와 다른 값을 사용합니다.

기존 일반 환경변수도 유지합니다.

- `ALLOWED_ORIGIN=https://jhj2525.github.io`
- `GITHUB_OWNER=jhj2525`
- `GITHUB_REPO=semicon-battery-brief`
- `GITHUB_BRANCH=main`

## 4. 확인

다음 정기 실행이 끝난 뒤 사이트에서 반도체·배터리 자동 뉴스가 각각 최대 5개인지 확인합니다.
기사 한 개를 삭제하면 현재 화면에서 바로 사라지고, 새로고침 후에도 보이지 않아야 합니다.
삭제한 자리는 다음 자동 묶음이 만들어질 때까지 비어 있어야 합니다.
