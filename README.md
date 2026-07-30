# Semiconductor & Battery Brief

반도체·배터리 산업 뉴스를 매일 자동으로 수집하고, 국내/해외 및 산업 분야별로 분류해 보여주는 정적 뉴스 대시보드입니다.

## 주요 기능

- 반도체 최대 5건, 배터리 최대 5건 선정
- 기본 구성: 국내 3건 + 해외 2건(후보 부족 시 유동 조정)
- 기술·공정·장비·소재 뉴스 우선
- 주가 전망·종목 추천 제외
- 제목 유사도를 이용한 중복 기사 제거
- Gemini API가 연결된 경우 한국어 요약과 공정기술 관점 생성
- 매일 오전 7시(KST) GitHub Actions 자동 실행

## 처음 설정

1. 이 폴더 안의 모든 파일을 GitHub 저장소 루트에 업로드합니다.
2. Google AI Studio에서 Gemini API 키를 발급합니다.
3. GitHub 저장소 `Settings → Secrets and variables → Actions → New repository secret`에서:
   - Name: `GEMINI_API_KEY`
   - Secret: 발급받은 API 키
4. `Actions` 탭에서 `Daily news update`를 선택하고 `Run workflow`로 최초 실행합니다.

API 키가 없어도 제목·RSS 설명 기반으로 수집되지만 AI 요약은 생성되지 않습니다.

## 공개 사이트 전환

저장소를 Public으로 전환한 뒤 `Settings → Pages`에서 GitHub Actions 배포를 활성화하면 무료 공개 사이트로 사용할 수 있습니다.

## 데이터 출처

현재 버전은 Google News RSS 검색 결과를 후보군으로 사용하고, 원문 제목·출처·링크를 표시합니다. 기사 전문을 저장하거나 재게시하지 않습니다.

