import { expect,test } from "@playwright/test";

function fixture(status="RECENT_CAPTURED") {
  return {mode:"PUBLIC_OBSERVATION_ONLY",status:"PERSISTED_STATUS_NOT_LIVENESS_PROOF",api_schema:1,action:"NO_TRADE",executable_ready:false,
    generated_at:1792763000,data_status:status,observation_age_seconds:status==="STALE" ? 1800 : 20,
    runtime:{state:"RUNNING",heartbeat_age_seconds:20,process_liveness:"UNVERIFIED"},blockers:["PE_UNAVAILABLE","CREDIT_PLAN_UNVERIFIED"],
    last_observation:{mode:"PUBLIC_OBSERVATION_ONLY",action:"NO_TRADE",executable_ready:false,symbol:"BTCTMN",observed_at:1792762980,decision_cutoff:1792762200,health:"RUNNING",
      orderbook:{best_bid:"100",best_ask:"101",spread_pct:"0.995"},readiness_blockers:["PE_UNAVAILABLE","CREDIT_PLAN_UNVERIFIED"],observation_failures:[],
      pipeline:{validation:{h1_candles:264,m1_m15:{complete_m15:24}},summary:{evaluated_steps:4,technical_eligible_steps:0,replay_errors:0},
        evaluated_steps:[{as_of:1792762200,technical_eligible:false,blockers:["PE_UNAVAILABLE"],technical_evidence:{trend:{eligible:true,reasons:["TREND_CONDITIONS_MET"]},pullback:{eligible:false,reasons:["PREVIOUS_CLOSE_ABOVE_EMA20"]}}}]}}};
}

test("default nine-route backend view uses public evidence without demo financial or account data",async({page})=>{
  const errors:string[]=[];
  page.on("pageerror",error=>errors.push(error.message));
  await page.route("**/api/observer",route=>route.fulfill({json:fixture()}));
  await page.goto("/");
  await expect(page.getByText("264 / 24",{exact:true})).toBeVisible();
  await expect(page.locator(".workspace")).not.toContainText("12,067.20");
  for(const [label,hash] of [["نمای کلی","overview"],["اعتبار و ریسک","credit-risk"],["استراتژی و بازار","strategy-market"],["مرکز تصمیم","decision-center"],["موقعیت‌ها و سفارش‌ها","positions-orders"],["بک‌تست و تحلیل","backtest-analytics"],["کنترل ربات","robot-control"],["هشدارها و رویدادها","alerts-events"],["اتصال و تنظیمات","connections"]]) {
    await page.getByRole("link",{name:label,exact:true}).click();
    await expect(page).toHaveURL(new RegExp("#"+hash+"$"));
    await expect(page.getByRole("heading",{level:1,name:label,exact:true})).toBeVisible();
    await expect(page.locator(".trade-disabled")).toBeDisabled();
    await expect(page.locator(".workspace")).not.toContainText("12,067.20");
    await expect(page.locator(".workspace")).toContainText("NO_TRADE");
    if(hash==="decision-center") await expect(page.getByRole("region",{name:"ژورنال واقعیِ ارزیابی‌های فنی"})).toContainText("PREVIOUS_CLOSE_ABOVE_EMA20");
    if(hash==="credit-risk") await expect(page.locator(".workspace")).toContainText("UNAVAILABLE");
  }
  await expect(page.locator('input[type="file"]')).toHaveCount(0);
  await page.setViewportSize({width:390,height:900});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.getByRole("button",{name:"پوسته روشن",exact:true}).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme","light");
  expect(errors).toEqual([]);
});

test("backend polling has loading error stale and unavailable states with no DEMO fallback",async({page})=>{
  await page.clock.install();
  let reads=0;
  await page.route("**/api/observer",async route=>{
    reads++;
    if(reads===1) await route.fulfill({json:fixture()});
    else if(reads===2) await route.fulfill({json:fixture("STALE")});
    else await route.abort();
  });
  await page.goto("/");
  await expect(page.getByRole("region",{name:"اتصال واقعی به Python · دادهٔ عمومی"})).toContainText("RECENT_CAPTURED");
  await page.clock.fastForward(16000);
  await expect(page.getByRole("region",{name:"اتصال واقعی به Python · دادهٔ عمومی"})).toContainText("STALE");
  await page.getByRole("button",{name:"دریافت دوباره از API"}).click();
  await expect(page.locator(".workspace")).toContainText("API_UNAVAILABLE_OR_INVALID");
  await expect(page.locator(".workspace")).not.toContainText("12,067.20");
  await page.route("**/api/observer",route=>route.fulfill({status:503,json:{...fixture(),data_status:"UNAVAILABLE",last_observation:null,blockers:["OBSERVER_STATE_UNAVAILABLE_OR_INVALID"]}}));
  await page.getByRole("button",{name:"دریافت دوباره از API"}).click();
  await expect(page.locator(".workspace")).toContainText("OBSERVER_UNAVAILABLE");
  await expect(page.locator(".workspace")).not.toContainText("264 / 24");
  expect(reads).toBeGreaterThanOrEqual(3);
});
