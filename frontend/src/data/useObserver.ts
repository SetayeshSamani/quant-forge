import { useEffect, useState } from "react";
import { parseEvidence, type RuntimeEvidence } from "./runtimeEvidence";

export interface AiReview { status:string; decision:string; requestedModel:string | null; responseModel:string | null; evidenceHash:string; sourceCommit:string; asOf:number; completedAt:number; source:string; evidenceKind:string; reasons:string[] }

export interface ObserverConnection {
  loading: boolean; error: string | null; status: string; checkedAt: number | null;
  generatedAt: number | null; age: number | null; runtimeState: string;
  heartbeatAge: number | null; blockers: string[]; refresh: () => void;
  market: RuntimeEvidence | null; aiReviews:AiReview[]; localChecks:Record<string,string>;
  steps: { cutoff: number | null; eligible: boolean; reasons: string[] }[];
}
const record = (v: unknown): Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {};
const number = (v: unknown): number | null => typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : null;
const codes = (v: unknown): string[] => Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && /^[A-Za-z0-9_:[\]./-]{1,160}$/.test(x)).slice(0,100) : [];
export function useObserver(enabled: boolean): ObserverConnection {
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<Omit<ObserverConnection,"refresh">>({ loading:true,error:null,status:"LOADING",checkedAt:null,
    generatedAt:null,age:null,runtimeState:"UNAVAILABLE",heartbeatAge:null,blockers:[],market:null,steps:[],aiReviews:[],localChecks:{} });
  useEffect(() => {
    if (!enabled) return;
    let active=true, timer: ReturnType<typeof setTimeout>, controller: AbortController;
    async function read() {
      controller=new AbortController();
      const timeout=setTimeout(() => controller.abort(),5000);
      setState(old => ({...old,loading:true}));
      try {
        const response=await fetch("/api/observer",{signal:controller.signal,cache:"no-store",credentials:"omit",redirect:"error"});
        const text=await response.text();
        if(text.length>1048576) throw new Error("INVALID_API_RESPONSE");
        const data=record(JSON.parse(text));
        if(data.api_schema!==1 || data.mode!=="PUBLIC_OBSERVATION_ONLY" || data.action!=="NO_TRADE" || data.executable_ready!==false || ![200,503].includes(response.status)) throw new Error("INVALID_API_RESPONSE");
        const parsed=data.last_observation===null ? null : parseEvidence(text);
        if(parsed && parsed.kind!=="public") throw new Error("INVALID_API_RESPONSE");
        const runtime=record(data.runtime), pipeline=record(record(data.last_observation).pipeline);
        const sourceSteps=Array.isArray(pipeline.evaluated_steps) ? pipeline.evaluated_steps.slice(-128) : [];
        const ai=record(data.ai_review);
        const aiReviews:AiReview[]=(Array.isArray(ai.records) ? ai.records.slice(0,5) : []).flatMap(value=>{
          const row=record(value);
          if(row.action!=="NO_TRADE" || row.executable_ready!==false || typeof row.evidence_hash!=="string" || !/^[0-9a-f]{64}$/.test(row.evidence_hash)
             || typeof row.source_commit!=="string" || !/^[0-9a-f]{40}$/.test(row.source_commit) || number(row.as_of)===null || number(row.completed_at)===null) return [];
          return [{status:typeof row.status==="string" ? row.status : "UNAVAILABLE",decision:row.advisory_decision==="REVIEW" ? "REVIEW" : "NO_TRADE",
            requestedModel:typeof row.requested_model==="string" ? row.requested_model : null,responseModel:typeof row.response_model==="string" ? row.response_model : null,
            evidenceHash:row.evidence_hash,sourceCommit:row.source_commit,asOf:number(row.as_of)!,completedAt:number(row.completed_at)!,
            source:row.inference_source==="BOXAPI_HTTPS" ? "BOXAPI_HTTPS" : "SYNTHETIC_TEST_TRANSPORT",evidenceKind:row.evidence_kind==="historical_data" ? "HISTORICAL / OFFLINE" : "SYNTHETIC",reasons:codes(row.reason_codes)}];
        });
        const checks=record(data.local_readonly_checks);
        const localChecks:Record<string,string>={};
        for(const key of ["mode","account","scope","balances","orders","fees","market_rules","risk","execution"]){
          const value=checks[key];
          if(typeof value==="string" && /^[A-Z][A-Z0-9_]{0,90}$/.test(value))localChecks[key]=value;
        }
        if(active) setState({loading:false,error:response.ok ? null : "OBSERVER_UNAVAILABLE",status:data.data_status==="RECENT_CAPTURED" ? "RECENT_CAPTURED" : data.data_status==="STALE" ? "STALE" : "UNAVAILABLE",
          checkedAt:Math.floor(Date.now()/1000),generatedAt:number(data.generated_at),age:number(data.observation_age_seconds),
          runtimeState:typeof runtime.state==="string" && /^(STARTING|RUNNING|DEGRADED|STOPPED|FAILED)$/.test(runtime.state) ? runtime.state : "UNAVAILABLE",
          localChecks,aiReviews,heartbeatAge:number(runtime.heartbeat_age_seconds),blockers:codes(data.blockers),market:parsed as RuntimeEvidence | null,
          steps:sourceSteps.map(value => {const step=record(value), tech=record(step.technical_evidence); return {cutoff:number(step.as_of),eligible:step.technical_eligible===true,
            reasons:[...codes(record(tech.trend).reasons),...codes(record(tech.pullback).reasons),...codes(step.blockers)]};})});
      } catch {
        if(active) setState(old=>({...old,loading:false,error:"API_UNAVAILABLE_OR_INVALID",status:old.market ? "STALE" : "UNAVAILABLE",checkedAt:Math.floor(Date.now()/1000)}));
      } finally {
        clearTimeout(timeout);
        if(active) timer=setTimeout(read,15000);
      }
    }
    void read();
    return () => {active=false;clearTimeout(timer);controller?.abort();};
  },[enabled,revision]);
  return {...state,refresh:()=>setRevision(v=>v+1)};
}
