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

```bash
fly launch --no-deploy   # fly.toml이 이미 있으므로 기존 설정을 사용하도록 선택
fly secrets set \
  SUPABASE_URL=... \
  SUPABASE_ANON_KEY=... \
  SUPABASE_SERVICE_ROLE_KEY=... \
  ANTHROPIC_API_KEY=... \
  SESSION_SECRET=...
fly deploy
```

`fly.toml`은 `primary_region = "nrt"`(도쿄), 헬스체크 경로 `/healthz`가 이미 설정되어 있습니다. 앱 이름이 이미 사용 중이면 `fly.toml`의 `app` 값을 원하는 이름으로 변경하세요.

## 5. 정책 요약

- **승인 플로우**: 가입 즉시 로그인은 가능하나 생성 기능은 차단되고 대기 화면이 표시됩니다. 관리자가 `/admin`에서 승인/거절합니다.
- **사용 한도**: 1인당 가입일 기준 30일 롤링 윈도우당 200건 (최초 생성 + 재생성/수정 요청 모두 포함). 초과 시 다음 리셋일까지 완전 차단됩니다.
- **민감정보**: 주민등록번호 패턴은 정규식으로 자동 차단됩니다. 그 외 민감정보(가족관계/건강/상담 등)는 자동 탐지 없이 안내 문구로만 고지합니다.
- **데이터 보관**: 생성 결과는 만료 없이 영구 보관되며, 사용자가 계정 삭제를 요청하면 즉시 하드 삭제(생성 이력 + 프로필 + Supabase Auth 계정)됩니다.
- **RLS**: `profiles`/`generations` 모두 본인 행만 조회 가능하도록 RLS가 설정되어 있습니다. 관리자 승인/모니터링은 서버가 `service_role` 키로 RLS를 우회해 처리하며, 이 키는 서버 환경변수로만 보관됩니다.

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
