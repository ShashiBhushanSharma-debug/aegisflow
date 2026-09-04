-- AegisFlow schema. Apply to a fresh Postgres 17 database:
--   psql "$DATABASE_URL" -f schema.sql

--
-- PostgreSQL database dump
--

\restrict ardWRQgDxkdpfmoi0Up505VUfcSyxrPX4Gps8KEATxsjFgf4PZZxJpN7fUhYz8E

-- Dumped from database version 17.6
-- Dumped by pg_dump version 17.11 (Ubuntu 17.11-1.pgdg24.04+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- Name: dispute_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.dispute_status AS ENUM (
    'RECEIVED',
    'TRIAGED',
    'EVIDENCE_REQUESTED',
    'EVIDENCE_VALIDATED',
    'RULES_RESOLVED',
    'RECOVERY_ESTIMATED',
    'CASE_SYNTHESIZED',
    'POLICY_CHECK',
    'SUBMITTED',
    'HUMAN_REVIEW',
    'ACCEPTED',
    'CLOSED',
    'APPROVED',
    'REJECTED',
    'SUBMIT_FAILED',
    'FAILED',
    'DRAFTED',
    'CONCEDED',
    'WON',
    'LOST',
    'BLOCKED'
);


--
-- Name: evidence_validation_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.evidence_validation_status AS ENUM (
    'PENDING',
    'VERIFIED',
    'REJECTED',
    'CONTRADICTORY',
    'UNVERIFIED'
);


--
-- Name: policy_action; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.policy_action AS ENUM (
    'AUTO_SUBMIT',
    'AUTO_DRAFT',
    'HUMAN_REVIEW',
    'ACCEPT_LOSS',
    'FIGHT',
    'ACCEPT',
    'REVIEW'
);


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: agent_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_runs (
    run_id character varying(64) DEFAULT (extensions.uuid_generate_v4())::text NOT NULL,
    case_id character varying(64),
    agent_name character varying(64) NOT NULL,
    model_version character varying(64) NOT NULL,
    prompt_version character varying(32) NOT NULL,
    status character varying(32) NOT NULL,
    latency_ms integer NOT NULL,
    input_payload jsonb,
    output_payload jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: cases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cases (
    case_id character varying(64) NOT NULL,
    merchant_id character varying(64) NOT NULL,
    dispute_id character varying(64) NOT NULL,
    payment_id character varying(64) NOT NULL,
    order_id character varying(64) NOT NULL,
    amount numeric(12,2) NOT NULL,
    currency character varying(3) DEFAULT 'INR'::character varying,
    reason_code character varying(100) NOT NULL,
    status public.dispute_status DEFAULT 'RECEIVED'::public.dispute_status,
    deadline timestamp with time zone NOT NULL,
    case_version integer DEFAULT 1,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    reviewer text,
    review_notes text,
    reviewed_at timestamp with time zone,
    error text,
    razorpay_response jsonb,
    rejection_reason text,
    razorpay_doc_ids jsonb,
    submitted_at timestamp with time zone
);


--
-- Name: claims; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.claims (
    claim_id character varying(64) NOT NULL,
    case_id character varying(64),
    statement text NOT NULL,
    evidence_ids character varying(64)[] NOT NULL,
    is_grounded boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now(),
    approved_by text,
    approved_at timestamp with time zone
);


--
-- Name: events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.events (
    event_id character varying(64) NOT NULL,
    case_id character varying(64),
    event_type character varying(64) NOT NULL,
    payload_hash character varying(64) NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.evidence (
    evidence_id character varying(64) NOT NULL,
    case_id character varying(64),
    type character varying(64) NOT NULL,
    source character varying(64) NOT NULL,
    source_record_id character varying(128),
    content_hash character varying(64) NOT NULL,
    raw_payload jsonb NOT NULL,
    validation_status public.evidence_validation_status DEFAULT 'PENDING'::public.evidence_validation_status,
    validation_notes text,
    retrieved_at timestamp with time zone DEFAULT now(),
    razorpay_doc_id text
);


--
-- Name: policy_decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.policy_decisions (
    decision_id character varying(64) DEFAULT (extensions.uuid_generate_v4())::text NOT NULL,
    case_id character varying(64),
    policy_version character varying(32) NOT NULL,
    action public.policy_action NOT NULL,
    expected_recovery_value numeric(12,2) NOT NULL,
    win_probability numeric(5,4) NOT NULL,
    rationale jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: webhook_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.webhook_events (
    event_id text NOT NULL,
    event_type text,
    case_id text,
    received_at timestamp with time zone DEFAULT now()
);


--
-- Name: agent_runs agent_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_pkey PRIMARY KEY (run_id);


--
-- Name: cases cases_dispute_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cases
    ADD CONSTRAINT cases_dispute_id_key UNIQUE (dispute_id);


--
-- Name: cases cases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cases
    ADD CONSTRAINT cases_pkey PRIMARY KEY (case_id);


--
-- Name: claims claims_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claims
    ADD CONSTRAINT claims_pkey PRIMARY KEY (claim_id);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (event_id);


--
-- Name: evidence evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_pkey PRIMARY KEY (evidence_id);


--
-- Name: policy_decisions policy_decisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.policy_decisions
    ADD CONSTRAINT policy_decisions_pkey PRIMARY KEY (decision_id);


--
-- Name: webhook_events webhook_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.webhook_events
    ADD CONSTRAINT webhook_events_pkey PRIMARY KEY (event_id);


--
-- Name: idx_agent_runs_case; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_case ON public.agent_runs USING btree (case_id);


--
-- Name: idx_cases_dispute; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cases_dispute ON public.cases USING btree (dispute_id);


--
-- Name: idx_cases_merchant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cases_merchant ON public.cases USING btree (merchant_id);


--
-- Name: idx_claims_case; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_claims_case ON public.claims USING btree (case_id);


--
-- Name: idx_evidence_case; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_evidence_case ON public.evidence USING btree (case_id);


--
-- Name: agent_runs agent_runs_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(case_id) ON DELETE CASCADE;


--
-- Name: claims claims_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claims
    ADD CONSTRAINT claims_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(case_id) ON DELETE CASCADE;


--
-- Name: events events_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(case_id) ON DELETE CASCADE;


--
-- Name: evidence evidence_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(case_id) ON DELETE CASCADE;


--
-- Name: policy_decisions policy_decisions_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.policy_decisions
    ADD CONSTRAINT policy_decisions_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(case_id) ON DELETE CASCADE;


--
-- Name: agent_runs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agent_runs ENABLE ROW LEVEL SECURITY;

--
-- Name: cases; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cases ENABLE ROW LEVEL SECURITY;

--
-- Name: claims; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.claims ENABLE ROW LEVEL SECURITY;

--
-- Name: evidence; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.evidence ENABLE ROW LEVEL SECURITY;

--
-- Name: policy_decisions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.policy_decisions ENABLE ROW LEVEL SECURITY;

--
-- Name: webhook_events; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.webhook_events ENABLE ROW LEVEL SECURITY;

--
-- PostgreSQL database dump complete
--

\unrestrict ardWRQgDxkdpfmoi0Up505VUfcSyxrPX4Gps8KEATxsjFgf4PZZxJpN7fUhYz8E

