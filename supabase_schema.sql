-- RescueLens MVP database schema
-- Run this in Supabase SQL Editor before deploying.

create table if not exists emergencies (
  id text primary key,
  display_number integer unique not null,
  description text not null,
  reporter_name text,
  reporter_phone text,
  latitude double precision not null,
  longitude double precision not null,
  location text,
  emergency_type text not null,
  priority text not null,
  required_team text not null,
  people_affected integer not null default 1,
  medical_emergency boolean not null default false,
  people_trapped boolean not null default false,
  critical_injury boolean not null default false,
  ai_summary text,
  ai_reasoning text,
  ai_generated boolean not null default false,
  status text not null default 'Waiting for responder',
  created_at timestamptz not null,
  updated_at timestamptz not null
);

create table if not exists responders (
  id text primary key,
  name text not null,
  type text not null,
  latitude double precision not null,
  longitude double precision not null,
  available boolean not null default true
);

create table if not exists assignments (
  id text primary key,
  emergency_id text unique not null references emergencies(id) on delete cascade,
  responder_id text not null references responders(id),
  distance_km double precision,
  eta_minutes double precision,
  created_at timestamptz not null
);

alter table emergencies enable row level security;
alter table responders enable row level security;
alter table assignments enable row level security;

-- Demo/MVP policies. For a production emergency system, replace these
-- with authenticated, role-based policies.
create policy "demo read emergencies" on emergencies for select using (true);
create policy "demo insert emergencies" on emergencies for insert with check (true);
create policy "demo update emergencies" on emergencies for update using (true);

create policy "demo read responders" on responders for select using (true);
create policy "demo update responders" on responders for update using (true);
create policy "demo insert responders" on responders for insert with check (true);

create policy "demo read assignments" on assignments for select using (true);
create policy "demo insert assignments" on assignments for insert with check (true);
create policy "demo delete assignments" on assignments for delete using (true);
