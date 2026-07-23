-- 생기부 도우미 데이터베이스 스키마
-- Supabase SQL Editor에서 그대로 실행하세요.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- profiles: auth.users 1:1 확장 테이블. 승인 상태 / 역할을 관리한다.
-- ---------------------------------------------------------------------------
create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  email text not null,
  display_name text,
  role text not null default 'user' check (role in ('user', 'admin')),
  status text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
  -- 이 교사가 담당하는 과목 목록(회원가입 또는 계정 설정에서 선택). 비어 있으면
  -- 앱에서 전체 과목 목록으로 대체해 보여준다(과거 가입자와의 하위 호환).
  subjects text[] not null default '{}',
  -- 회원가입 시 입력하는 소속 학교/기관명. korea.kr처럼 특정 기관을 특정할 수
  -- 없는 도메인으로 가입한 경우, 관리자가 승인 대기열에서 이 값을 보고
  -- 소속을 육안으로 대조해 확인하는 용도로 쓴다.
  school_name text,
  created_at timestamptz not null default now(),
  approved_at timestamptz
);

-- 기존에 테이블이 이미 있던 배포 환경에서도 안전하게 재실행 가능하도록 추가.
alter table public.profiles add column if not exists subjects text[] not null default '{}';
alter table public.profiles add column if not exists school_name text;

alter table public.profiles enable row level security;

-- 본인 프로필만 조회 가능. 승인/역할 변경은 앱 서버가 service-role 키로만 수행하므로
-- 사용자에게는 update 권한을 주지 않는다 (자기 자신을 승인/관리자로 바꾸는 것을 원천 차단).
create policy "profiles_select_own" on public.profiles
  for select using (auth.uid() = id);

-- 신규 가입 시 profiles row를 자동 생성하는 트리거
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.profiles (id, email)
  values (new.id, new.email)
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

-- ---------------------------------------------------------------------------
-- generations: 생성 요청/결과 이력. 사용량 한도 계산의 기준이 되는 테이블.
-- ---------------------------------------------------------------------------
create table if not exists public.generations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles (id) on delete cascade,
  student_label text not null,
  category text,
  input_text text not null,
  output_text text not null,
  model text not null,
  created_at timestamptz not null default now()
);

create index if not exists generations_user_id_created_at_idx
  on public.generations (user_id, created_at desc);

alter table public.generations enable row level security;

create policy "generations_select_own" on public.generations
  for select using (auth.uid() = user_id);

create policy "generations_insert_own" on public.generations
  for insert with check (auth.uid() = user_id);

-- ---------------------------------------------------------------------------
-- usage_ledger: 이메일 단위로 사용량 주기/카운트를 영구 보관한다.
-- profiles/generations와 달리 계정을 하드 삭제해도 이 테이블은 지우지 않는다.
-- 같은 이메일로 재가입해도 사용 한도가 초기화되지 않도록 하기 위함
-- (재가입 시에는 어차피 승인 대기 상태로 돌아가 관리자 재승인이 필요하다).
-- 일반 사용자에게는 어떤 접근 정책도 주지 않아, service-role 키로만 조작 가능하다.
-- ---------------------------------------------------------------------------
create table if not exists public.usage_ledger (
  email text primary key,
  period_start timestamptz not null default now(),
  generation_count integer not null default 0,
  -- 관리자가 이 교사에게만 개별로 지정한 30일 주기 사용 한도. null이면 전역
  -- 기본값(MONTHLY_LIMIT 환경변수)을 따른다.
  monthly_limit integer,
  updated_at timestamptz not null default now()
);

-- 기존에 테이블이 이미 있던 배포 환경에서도 안전하게 재실행 가능하도록 추가.
alter table public.usage_ledger add column if not exists monthly_limit integer;

alter table public.usage_ledger enable row level security;

-- 일반 사용자(anon/authenticated)의 접근을 명시적으로 차단하는 정책.
-- service-role 키는 RLS를 우회하므로 서버 쪽 접근에는 영향이 없다.
-- (정책이 하나도 없으면 Supabase Advisor가 "RLS Enabled No Policy" 경고를 띄운다.)
create policy "usage_ledger_no_client_access" on public.usage_ledger
  for all using (false);

-- ---------------------------------------------------------------------------
-- drafts: 생성 폼의 임시저장 데이터 + 글자수 다듬기 화면의 마지막 목표
-- 바이트 범위(adjust_min_char_limit/adjust_max_char_limit)를 함께 보관한다.
-- 사용자당 최대 1건만 보관한다(덮어쓰기).
-- ---------------------------------------------------------------------------
create table if not exists public.drafts (
  user_id uuid primary key references public.profiles (id) on delete cascade,
  data jsonb not null,
  updated_at timestamptz not null default now()
);

alter table public.drafts enable row level security;

create policy "drafts_select_own" on public.drafts
  for select using (auth.uid() = user_id);

create policy "drafts_insert_own" on public.drafts
  for insert with check (auth.uid() = user_id);

create policy "drafts_update_own" on public.drafts
  for update using (auth.uid() = user_id);

create policy "drafts_delete_own" on public.drafts
  for delete using (auth.uid() = user_id);

-- ---------------------------------------------------------------------------
-- 최초 관리자 지정 (가입 후 1회 수동 실행, README 참고)
-- ---------------------------------------------------------------------------
-- update public.profiles set role = 'admin', status = 'approved', approved_at = now()
-- where email = 'your-admin-email@example.com';
