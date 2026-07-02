# 생기부 도우미

Claude API를 활용해 교사의 학생별 관찰 자료를 학교생활기록부(생기부) 서술형 문구로 변환해주는 웹서비스입니다.
승인제 회원가입, 30일 롤링 사용량 한도, 민감정보 1차 차단, 관리자 승인/모니터링 화면을 포함한 MVP입니다.

## 기술 스택

- 백엔드/프론트: Python, FastAPI + Jinja2 서버 렌더링
- 인증/DB: Supabase (PostgreSQL + Supabase Auth, RLS)
- AI: Anthropic Claude API (`claude-sonnet-5` 기본)
- 배포: Fly.io (도쿄 `nrt` 리전)

## 1. Supabase 설정

1. [Supabase](https://supabase.com)에서 새 프로젝트를 생성합니다.
2. Supabase 대시보드 **SQL Editor**에서 `supabase/schema.sql` 내용을 그대로 실행합니다.
   - `profiles`(승인 상태/역할), `generations`(생성 이력) 테이블과 RLS 정책, 신규 가입 시 프로필 자동 생성 트리거가 생성됩니다.
3. **Authentication > Providers**에서 이메일 인증(Confirm email) 사용 여부를 프로젝트 정책에 맞게 설정합니다.
   - 이메일 인증을 켜두면 사용자는 이메일 인증 후에도 관리자 승인이 있어야 생성 기능을 사용할 수 있습니다(이중 게이트).
4. **Project Settings > API**에서 `Project URL`, `anon public key`, `service_role key`를 확인해 둡니다. `service_role key`는 절대 클라이언트/저장소에 노출하지 마세요.

### 최초 관리자 지정

일반 가입 절차로 관리자 계정도 먼저 가입한 뒤, SQL Editor에서 아래 쿼리로 승격합니다.

```sql
update public.profiles
set role = 'admin', status = 'approved', approved_at = now()
where email = 'your-admin-email@example.com';
```

## 2. 환경 변수

```bash
cp .env.example .env
```

`.env` 파일을 열어 다음 값을 채웁니다.

| 변수 | 설명 |
| --- | --- |
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` / `SUPABASE_SERVICE_ROLE_KEY` | Supabase 프로젝트 설정값 |
| `ANTHROPIC_API_KEY` | 본 프로젝트 전용으로 발급한 Anthropic API 키 (관리자 개인 키 1개, 타 프로젝트와 공유 금지) |
| `ANTHROPIC_MODEL` | 기본 `claude-sonnet-5`, 필요 시 Opus/Haiku로 전환 |
| `SESSION_SECRET` | 세션 쿠키 서명용 임의의 긴 문자열 |
| `MONTHLY_LIMIT` / `ROLLING_WINDOW_DAYS` | 사용량 한도 정책 (기본 30일당 200건) |

**비용 안전장치**: [Anthropic 콘솔](https://console.anthropic.com)에서 Spending Limit을 월 15만원으로 설정해 두세요. 이 값은 코드가 아닌 Anthropic 콘솔에서 직접 설정합니다.

## 3. 로컬 실행

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```

`http://localhost:8080` 접속 → 회원가입 → (SQL로 관리자 승인 또는 관리자 계정으로 로그인 후 `/admin`에서 승인) → 로그인 → 생성 기능 확인.

## 4. Fly.io 배포 (도쿄 리전)

### 최초 1회 설정

```bash
fly launch --no-deploy   # fly.toml이 이미 있으므로 기존 설정을 사용하도록 선택
fly secrets set \
  SUPABASE_URL=... \
  SUPABASE_ANON_KEY=... \
  SUPABASE_SERVICE_ROLE_KEY=... \
  ANTHROPIC_API_KEY=... \
  SESSION_SECRET=...
```

`fly secrets set`으로 등록한 값은 Fly.io 서버에 암호화되어 저장되며, 이후 배포(`fly deploy` 또는 GitHub Actions)에서 계속 유지되므로 매번 다시 등록할 필요가 없습니다. `fly.toml`은 `primary_region = "nrt"`(도쿄), 헬스체크 경로 `/healthz`가 이미 설정되어 있습니다. 앱 이름이 이미 사용 중이면 `fly.toml`의 `app` 값을 원하는 이름으로 변경하세요.

### GitHub Actions 자동 배포

`main` 브랜치(또는 저장소 기본 브랜치)에 push할 때마다 `.github/workflows/fly-deploy.yml` 워크플로우가 자동으로 `fly deploy`를 실행합니다.

1. Fly.io 배포 토큰 발급: `fly tokens create deploy -x 999999h`
2. GitHub 저장소 **Settings > Secrets and variables > Actions**에서 `FLY_API_TOKEN`이라는 이름으로 위 토큰 값을 등록합니다. (앱 자체의 `SUPABASE_*`, `ANTHROPIC_API_KEY` 등은 GitHub이 아니라 위 `fly secrets set`으로만 등록합니다.)
3. 이후 브랜치에 push하면 자동으로 배포됩니다. 저장소 기본 브랜치 이름이 `main`이 아니라면 `.github/workflows/fly-deploy.yml`의 `branches:` 값을 실제 기본 브랜치명으로 맞춰주세요.

수동 배포만 원한다면 워크플로우 파일을 삭제하고 로컬에서 `fly deploy`만 실행해도 됩니다.

## 5. 정책 요약

- **가입 제한**: 이메일 도메인이 한국 시도교육청 도메인(`app/email_domains.py`의 목록, 예: 인천 `@ice.go.kr`)이거나 그 학교 서브도메인인 경우에만 가입할 수 있습니다. 목록이 실제와 다르면 해당 파일만 수정하면 됩니다.
- **승인 플로우**: 가입 즉시 로그인은 가능하나 생성 기능은 차단되고 대기 화면이 표시됩니다. 관리자가 `/admin`에서 승인/거절합니다.
- **사용 한도**: 1인당 가입일 기준 30일 롤링 윈도우당 기본 200건(`MONTHLY_LIMIT` 환경변수, 최초 생성 + 재생성/수정 요청 모두 포함). 초과 시 다음 리셋일까지 완전 차단됩니다. 관리자(role=admin) 계정은 한도가 적용되지 않습니다. `/admin` 화면에서 관리자가 교사별로 한도를 개별 지정할 수 있으며(`usage_ledger.monthly_limit`), 비워두고 저장하면 전역 기본값으로 되돌아갑니다.
- **민감정보**: 주민등록번호 패턴은 정규식으로 자동 차단됩니다. 그 외 민감정보(가족관계/건강/상담 등)는 자동 탐지 없이 안내 문구로만 고지합니다.
- **과목/성취기준**: 과목을 선택하면 그 과목의 성취기준 코드(예: `12정01-01`) 목록이 활동별 드롭다운에 나타납니다. 과목·성취기준 데이터는 `app/subject_criteria.py`의 `SUBJECT_CRITERIA`에서 관리하며, 실제 교육과정 성취기준으로 교체해서 사용해야 합니다(현재 값은 예시입니다). 학업 성취도는 A~E 중에서 선택합니다.
- **학생 일괄 처리**: 화면에서 "+ 학생 추가"로 학생 블록을 늘려 한 번에 여러 명(기본 최대 20명, `app/generation.py`의 `MAX_STUDENTS_PER_BATCH`에서 조정 가능)을 순차적으로 생성할 수 있습니다. 사용 한도는 학생 수만큼 미리 확인하며, 부족하면 요청 전체를 거절합니다. 학생별로 생성이 실패해도 나머지 학생은 계속 처리되고, 실패한 학생만 결과 화면에 오류로 표시됩니다. 학번/과목/활동이 비어 있는 등 조건을 만족하지 못해 아예 생성이 시도되지 않은 학생도 어떤 학생이 왜 제외됐는지 화면에 표시됩니다. 학생이 많아져 화면이 길어지는 것을 막기 위해 학생 블록마다 접기/펼치기가 가능하고, "모두 접기"/"모두 펼치기" 버튼으로 한 번에 전환할 수 있습니다. "일괄 생성하기"를 누르면 처리 중임을 알 수 있도록 버튼이 비활성화되고 "생성 중입니다..." 문구로 바뀝니다.
- **재생성/중복 경고**: 같은 학번을 다시 생성해도 기존 생성 이력을 덮어쓰지 않고 새 이력으로 추가되며, 이미 이력이 있는 학번을 다시 생성하면 결과 화면에 경고가 표시됩니다. 한 학생의 여러 활동에 같은 성취기준을 중복 선택한 경우에도(생성 자체는 진행되지만) 결과 화면에 경고가 표시됩니다.
- **임시저장**: 입력 중인 학생 목록과 목표 바이트는 "임시저장" 버튼으로 서버(`drafts` 테이블)에 저장할 수 있습니다. 사용자당 최근 1건만 보관(덮어쓰기)되며, 로그인 후 대시보드에 접속하면 자동으로 마지막 임시저장 내용이 불러와집니다. 유효성 검사 없이 입력한 그대로 저장되므로 작성 도중에 저장해도 됩니다.
- **활동 개수**: 학생별로 활동은 1개로 고정되지 않고 화면에서 자유롭게 추가/삭제할 수 있습니다(최대 10개, `app/generation.py`의 `MAX_ACTIVITIES`에서 조정 가능).
- **생성 결과**: 입력한 교과 성취 수준과 활동들을 모두 종합해, "교과 성취 수준 → 수행 특기사항 → 교과 역량 → 수업 태도" 순서를 따르는 **하나의 세특 문단**으로 생성합니다(활동별로 나누지 않음).
- **바이트**: 목표 바이트(최소/최대)는 화면에서 교사가 직접 지정합니다(기본값 600~700바이트). 최대 바이트는 나이스 입력 필드 한도를 고려해 1000바이트를 넘을 수 없도록 서버에서 강제합니다(`app/generation.py`의 `HARD_MAX_CHAR_LIMIT`). 바이트 수는 나이스(NEIS) 공식 계산식(`=2*LENB(cell)-LEN(SUBSTITUTE(cell,CHAR(10),""))`)을 그대로 이식한 `app/charcount.py`의 `neis_byte_count()`로 계산해 화면에 표시합니다.
- **데이터 보관**: 생성 결과는 만료 없이 영구 보관되며, 사용자가 계정 삭제를 요청하면 즉시 하드 삭제(생성 이력 + 프로필 + Supabase Auth 계정)됩니다. 관리자 계정은 삭제할 수 없습니다. 같은 이메일로 재가입해 사용 한도를 우회하는 것을 막기 위해 **이메일 단위 사용 건수/주기 기록(`usage_ledger`)만은 삭제되지 않고 남습니다** (생성 문구 등 실제 개인정보 내용은 포함하지 않음).
- **RLS**: `profiles`/`generations`/`drafts` 모두 본인 행만 조회·수정 가능하도록 RLS가 설정되어 있습니다. 관리자 승인/모니터링은 서버가 `service_role` 키로 RLS를 우회해 처리하며, 이 키는 서버 환경변수로만 보관됩니다.
- **화면 구성**: 로그인하면 왼쪽에 사이드바가 표시되며, "생기부 문구 생성"(`/dashboard`), "생성 이력"(`/history`), "계정 삭제"(`/account`, 관리자 제외), "관리자 모드"(`/admin`, 관리자만)로 이동할 수 있습니다. 승인 대기 중에는 생성/이력 메뉴가 숨겨지고 계정 삭제만 노출됩니다.
- **생성 이력 검색/정렬**: `/history`에서 학번·영역·결과 내용을 대상으로 검색할 수 있고(대소문자 구분 없음), "일시"/"학번"/"영역" 열 제목을 클릭하면 해당 기준으로 오름차순/내림차순 정렬됩니다(다시 클릭하면 순서가 뒤집힘). 검색/정렬은 최근 500건(`app/generation.py`의 `HISTORY_FETCH_LIMIT`) 범위 내에서 서버가 처리합니다.

## 6. 향후 고려사항 (이번 MVP 범위 밖)

- Batch API 적용(입출력 50% 할인) 및 프롬프트 캐싱 고도화
- 후원 결제 연동(현재는 계좌/QR 안내만 정적 노출)
- 이메일 알림(승인 완료, 한도 임박 등)
- 관리자 다중 계정/권한 세분화, UI 디자인 고도화

## 7. 테스트

```bash
pip install -r requirements-dev.txt
pytest
```

`tests/`에는 사용량 롤링 윈도우 계산과 민감정보(주민등록번호) 탐지 로직에 대한 단위 테스트가 포함되어 있습니다.
