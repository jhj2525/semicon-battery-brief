# Semiconductor & Battery Brief

반도체·배터리 산업 뉴스를 매일 자동으로 수집하고, 공개된 기사 원문을 확인한 뒤 사실만 정리해 보여주는 뉴스 데이터베이스입니다.

## 주요 기능

- 반도체 최대 5건, 배터리 최대 5건 선정
- 기본 구성: 국내 3건 + 해외 2건(후보 부족 시 유동 조정)
- 기술·공정·장비·소재 뉴스 우선
- 주가 전망·종목 추천 제외
- 제목 유사도를 이용한 중복 기사 제거
- 직접 원문 URL을 제공하는 RSS만 후보 출처로 사용
- Gemini URL Context가 원문 접근에 성공한 기사만 수록
- 3~5문장 개요, 핵심 사실, 주요 수치·일정, 원문에 명시된 계획·영향 정리
- 원문에 없는 외부 지식·추측·직무 해석 생성 금지
- 반도체/배터리 탭, 국내/해외 및 주제 필터, 검색, 상세 펼치기
- 검증된 과거 기사를 누적 보관
- 매일 오전 7시(KST) GitHub Actions 자동 실행

## 처음 설정

1. 이 폴더 안의 모든 파일을 GitHub 저장소 루트에 업로드합니다.
2. Google AI Studio에서 Gemini API 키를 발급합니다.
3. GitHub 저장소 `Settings → Secrets and variables → Actions → New repository secret`에서:
   - Name: `GEMINI_API_KEY`
   - Secret: 발급받은 API 키
4. `Actions` 탭에서 `Daily news update`를 선택하고 `Run workflow`로 최초 실행합니다.

API 키가 없으면 원문 검증 요약을 실행할 수 없으므로 자동화가 중단됩니다.

## 공개 사이트 전환

저장소를 Public으로 전환한 뒤 `Settings → Pages`에서 GitHub Actions 배포를 활성화하면 무료 공개 사이트로 사용할 수 있습니다.

## 데이터 출처

현재 버전은 원문으로 직접 연결되는 전문매체 RSS를 후보군으로 사용합니다. 기사 전문을 저장하거나 재게시하지 않으며, 요약과 원문 링크만 제공합니다. 유료벽·로그인·접근 차단으로 원문 확인에 실패한 기사는 제외합니다.
