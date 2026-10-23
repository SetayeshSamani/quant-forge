import { useEffect, useState } from "react";
import { parseEvidence, type RuntimeEvidence } from "./runtimeEvidence";

export interface ObserverConnection {
  loading: boolean; error: string | null; status: string; checkedAt: number | null;
  generatedAt: number | null; age: number | null; runtimeState: string;
  heartbeatAge: number | null; blockers: string[]; refresh: () => void;
  market: RuntimeEvidence | null;
  steps: { cutoff: number | null; eligible: boolean; reasons: string[] }[];
}
const record = (v: unknown): Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {};
const number = (v: unknown): number | null => typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : null;
const codes = (v: unknown): string[] => Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && /^[A-Za-z0-9_:[\]./-]{1,160}$/.test(x)).slice(0,100) : [];
export function useObserver(enabled: boolean): ObserverConnection {
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<Omit<ObserverConnection,"refresh">>({ loading:true,error:null,status:"LOADING",checkedAt:null,
    generatedAt:null,age:null,runtimeState:"UNAVAILABLE",heartbeatAge:null,blockers:[],market:null,steps:[] });
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
        if(active) setState({loading:false,error:response.ok ? null : "OBSERVER_UNAVAILABLE",status:data.data_status==="RECENT_CAPTURED" ? "RECENT_CAPTURED" : data.data_status==="STALE" ? "STALE" : "UNAVAILABLE",
          checkedAt:Math.floor(Date.now()/1000),generatedAt:number(data.generated_at),age:number(data.observation_age_seconds),
          runtimeState:typeof runtime.state==="string" && /^(STARTING|RUNNING|DEGRADED|STOPPED|FAILED)$/.test(runtime.state) ? runtime.state : "UNAVAILABLE",
          heartbeatAge:number(runtime.heartbeat_age_seconds),blockers:codes(data.blockers),market:parsed as RuntimeEvidence | null,
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
