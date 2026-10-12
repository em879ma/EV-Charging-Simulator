# Unobserved-behavior synthesis table (Person C)

Data: `caltech_sessions_PARTIAL.json`, site 0002, 23,200 sessions, 2018-04-25 to 2019-09-17. Evidence values are computed by `build_table.py`; rerun it when the full download lands.

Priority: **P0** needed for the baseline model, **P1** needed for a proposal scenario or policy, **P2** extension.

## A. Demand the data cannot see (blocked drivers)

### R01 · Unconstrained arrival demand when the site is full (满站时未被记录的真实到达需求) · P0

- **Needed for:** Arrival generator for the 30/42-port and 120-150% demand scenarios; 'blocked or queued vehicles' output
- **Why it is not in the data:** ACN-Data records only sessions that plugged in. A driver who found no free port, or stayed away because the garage is known to be full, leaves no record.
- **Evidence from ACN-Data:** Max concurrent plugged-in sessions = 51 of 54 ports (51 before 2018-11-01, 43 after); stations in use per month ranged 42-54. Occupancy came within 2 ports of the monthly in-service count in 0.00% of minutes, and 0.00% of arrivals saw a nearly full site.
- **Synthesis method:** Where the evidence shows capacity never bound, treat observed plug-ins as unconstrained demand at the observed capacity. Generate arrivals from the joint demand model BEFORE applying port limits, so the simulator produces blocking in counterfactual scenarios. If near-full periods appear in other data (e.g. JPL), estimate each time bin's arrival rate as plug-ins divided by the time at least one port was free (a censored-Poisson estimator; our derivation).
- **Baseline / range:** Unconstraining factor 1.0 / Covered by demand factor 0.8-1.5 (R17)
- **Scientific basis:** Unconstraining methods (projection detruncation, EM, censored-Poisson MLE) recover demand hidden by a binding capacity (review: Guo et al. 2012), and EV charging records are censored in the same way when stations are occupied (Hüttel et al. 2023). Neither correction is needed where capacity never binds (see the evidence column for the current data). The larger demand uncertainty is the pricing regime: sessions per day fell significantly when charging started to cost $0.12/kWh on 2018-11-01 (Lee et al. 2019).
- **References:** Guo et al. 2012; Hüttel et al. 2023; Lee et al. 2019
- **Check:** Rerun on the complete Caltech download and on JPL (employee-only, highly utilized). If near-full minutes appear, apply the censored-Poisson or EM correction to those bins.
- **Depends on:** Person A (complete download); Person B (arrival model)

### R02 · Balking probability when every port is occupied (满站时直接离开（balk）的概率) · P0

- **Needed for:** Blocked/queued vehicles, share of sessions fully served and % energy delivered when ports < demand
- **Why it is not in the data:** A driver who leaves a full site leaves no record, and the garage has no physical queue.
- **Evidence from ACN-Data:** 0 of 23,200 recorded arrivals met a site within 2 ports of the ports in service (0.00%); balking could only be observed in such episodes, and a driver who balked leaves no record anyway.
- **Synthesis method:** Scenario parameter p_balk (BlockedDriverBehavior). Baseline p_balk = 1: a blocked workplace driver parks in a regular space and does not charge (Erlang-loss behavior). p_balk < 1 sends the rest to a virtual waitlist (R03).
- **Baseline / range:** 1.0 (pure loss) / 0 (everyone joins a waitlist) to 1.0
- **Scientific basis:** EV parking-lot queueing models treat an arrival that finds every charger-equipped space occupied as lost, i.e. an Erlang loss system (Aveklouris et al. 2017). California workplace drivers describe the same behavior at unmanaged sites: late arrivals simply cannot charge (Sutton et al. 2022).
- **References:** Aveklouris et al. 2017; Sutton et al. 2022; Nicholas & Tal 2013
- **Check:** With Poisson arrivals, simulated blocking must match Erlang-B for the offered load (arrival rate x mean dwell), a clean engine check at 30/42/54/66 ports. External plausibility target: 38% of California drivers with workplace charging reported being unable to charge at least once a week because of congestion (Nicholas & Tal 2013).
- **Depends on:** Simulator engine (blocking event)

### R03 · Waitlist patience and plug-in hold time (候补排队的耐心时间与接到通知后的挪车时间) · P1

- **Needed for:** Queued-vehicle output; waitlist and move-after-charge handoff policies
- **Why it is not in the data:** No waiting was ever recorded; ACN had no waitlist.
- **Evidence from ACN-Data:** 0.00% of arrivals met a nearly full site and ACN ran no waitlist, so no waiting episodes are recorded.
- **Synthesis method:** Virtual waitlist (BlockedDriverBehavior): a driver who does not balk parks elsewhere and stays on a first-come-first-served list until their own departure. When a port frees, the notified driver needs Uniform(0, hold) minutes to move and plug in; the port is reserved and idle meanwhile. An 'impatient' variant uses exponential patience with mean theta.
- **Baseline / range:** Patience = own remaining dwell; hold = 15 min / Hold 15-30 min; impatient theta 10-60 min (assumption)
- **Scientific basis:** Operator waitlists hold a freed port for the notified driver for a fixed window: 15 min in ChargePoint Waitlist, and 30 min at Cal Poly's campus program, which opens its waitlist only 10:00-16:00. If waitlist logs become available, patience can be estimated from censored waiting times with Kaplan-Meier, as in call-center practice (Brown et al. 2005).
- **References:** ChargePoint Waitlist FAQ; Cal Poly TAPS; Brown et al. 2005
- **Check:** Count reserved-but-idle hold time as lost port time. If U-M LTP can provide waitlist logs, estimate patience with Kaplan-Meier instead of assuming it.
- **Depends on:** R02

### R04 · Return (retrial) probability and delay after being blocked (被挡后稍后再来的概率与间隔) · P2

- **Needed for:** Blocked vehicles that still get charged later; peak shifting under congestion
- **Why it is not in the data:** A driver who left and came back appears as one ordinary session; the failed first attempt is invisible.
- **Evidence from ACN-Data:** Not identifiable from session records.
- **Synthesis method:** One retry with probability p_retry after an exponential delay (BlockedDriverBehavior). Baseline 0 so that lost demand is not understated.
- **Baseline / range:** p_retry = 0; delay mean 180 min (assumption: back around lunch) / p_retry 0-0.5; delay mean 60-240 min (assumption)
- **Scientific basis:** One interviewed workplace driver who missed a morning slot reported checking again around lunchtime; this is an anecdote, not a measured pattern (Sutton et al. 2022). No quantitative retrial rate or delay has been published, so both are sensitivity parameters.
- **References:** Sutton et al. 2022
- **Check:** Compare energy delivered and blocked-vehicle counts for p_retry = 0 and 0.5.
- **Depends on:** R02

### R05 · Arrival-time shift under perceived scarcity (感知“桩不够”后改变到达时间的行为) · P2

- **Needed for:** Arrival profile in high-demand / few-port scenarios
- **Why it is not in the data:** The observed arrival profile was produced by a site that never filled.
- **Evidence from ACN-Data:** Occupancy was within 2 ports of the ports in service in 0.00% of minutes, so the observed arrival profile contains little or no response to scarcity.
- **Synthesis method:** Optional scenario: when simulated blocking on a day type exceeds a threshold, move a share s of 7-10 h arrivals to early morning (5-7 h) or evening. Off in the baseline.
- **Baseline / range:** Off / s = 0-0.08 of sessions
- **Scientific basis:** A field experiment with 629 drivers at UC San Diego found that discounts moved plug-ins toward early-morning and overnight hours, and a scarcity prime moved some plug-ins from early morning to evening; the authors attribute part of the timing response to perceived charger scarcity. Working-paper effects are a few percent of weekly sessions (first discount phase: early morning +4.3%, overnight +3.3%), several significant only at the 10% level (Garg et al. 2024; published as Garg et al. 2026).
- **References:** Garg et al. 2024 (WP); Garg et al. 2026
- **Check:** Sensitivity only; do not calibrate on Caltech.
- **Depends on:** Person B (arrival model)

## B. Responses to policies that were never in place

### R06 · Compliance with a move-after-charge rule (“充完即挪车”规则的遵守率) · P1

- **Needed for:** Move-after-charge policy; post-charge idle occupancy output
- **Why it is not in the data:** Caltech had no move-after-charge rule or idle fee during collection, so the response cannot be observed.
- **Evidence from ACN-Data:** Without any rule, 75% of completed sessions stayed more than 1 h after charging ended (median 192 min). Idle time is 48% of occupied port-minutes and 55% on weekday afternoons under Caltech's adaptive scheduler. Faster (e.g. uncontrolled) charging finishes earlier and leaves more idle time, so this is a LOWER bound on the port time a rule could recover; take the recoverable share from the simulator under each policy.
- **Synthesis method:** MoveAfterChargeResponse: a compliant driver unplugs at min(natural idle, grace + response delay); a non-compliant driver keeps the natural idle time. Natural idle must come from the simulated session under the same charging policy. Report the policy benefit as a function of gamma, including the break-even gamma at which moving cars beats adding ports.
- **Baseline / range:** gamma = 0.6 (assumption) / 0.2 (voluntary, unenforced) to 1.0 (enforced or valet)
- **Scientific basis:** At a workplace whose host encouraged but did not enforce moving after charging (INL 2014), Level-2 cords were connected 5.6 h per event but drew power for only 2.9 h (Francfort et al. 2014); the report notes it is unknown how far the policy changed behavior. Another workplace site had 38% of plugged-in time with no charging (Bae et al. 2020). Driver responses to penalties are heterogeneous in both a penalty-tolerance model (Biswas et al. 2016) and a stated-preference latent-class study (Wolbertus & Gerzon 2018), and a workplace field experiment tested fees and etiquette messages (Asensio et al. 2022). Zeng et al. (2019) model operator-performed interchange; treating an enforced rule as gamma near 1 is our assumption. No validated compliance probability exists.
- **References:** INL 2014; Francfort et al. 2014 (INL); Bae et al. 2020; Biswas et al. 2016; Wolbertus & Gerzon 2018; Asensio et al. 2022; Zeng et al. 2019
- **Check:** Sweep gamma over the full range; the break-even gamma is itself a reportable result.
- **Depends on:** Simulator (charging completion under each policy)

### R07 · Grace period and response delay after a charge-complete notice (宽限期与收到“充完”通知后的反应时间) · P1

- **Needed for:** Move-after-charge policy
- **Why it is not in the data:** No notification-based rule existed.
- **Evidence from ACN-Data:** Unplugging 2-12 min after completion is 1.95x the next five 10-min bins for sessions without an announced departure (placebo with completion moved 60 min later: 1.39x). 9.1% of completed sessions unplug within 15 min of completion with no rule at all.
- **Synthesis method:** Response delay ~ lognormal (median d, shape 0.8) after a grace period; d = 0 represents automated interchange only.
- **Baseline / range:** Grace 15 min; delay median 10 min (assumptions) / Grace 5-30 min; delay median 0 (automated) to 60 min
- **Scientific basis:** No field measurement of the delay from a charge-complete notice to moving the car has been published, and no workplace grace period was verified, so both are assumptions. For scale: ChargePoint Waitlist holds a freed port 15 min for the NEXT driver, and Cal Poly gives the next driver 30 min to plug in and notifies charged drivers to move when charging completes or at its 4-h cap. Interchange delay can be about zero only with automatic machinery; with people involved it takes minutes to hours (Zeng et al. 2019).
- **References:** ChargePoint Waitlist FAQ; Cal Poly TAPS; Zeng et al. 2019
- **Check:** Check the sensitivity of idle occupancy to grace in 5-30 min and delay in 0-60 min.
- **Depends on:** R06

### R08 · Whether departure depends on charging completion (离开时间是否取决于“充完”) · P0

- **Needed for:** Modeling assumption for every policy that changes completion times (equal sharing, earliest-departure-first)
- **Why it is not in the data:** Completion times were produced by Caltech's adaptive scheduler; a different policy changes completion and may change when some drivers leave.
- **Evidence from ACN-Data:** Completion-triggered unplugging is present but small: unplugs 2-12 min after completion exceed the background level by 3.1% of completed sessions (spike ratio 1.87 vs placebo 1.33), while 75% stay more than 1 h after completion.
- **Synthesis method:** Sample dwell as exogenous (park-and-charge) for all sessions. Sensitivity (DepartureResponse): a responsive share rho unplugs at min(planned departure, completion + Exp(10 min)) under every policy.
- **Baseline / range:** rho = excess share above background (scenario_parameters.csv) / rho 0 to 2x baseline
- **Scientific basis:** Connection time at Level-2 stations follows parking purpose, with time of day the strongest predictor (Wolbertus et al. 2018). In Dutch public data the 'charge near work' cluster (same-day departures) stays 8 h 42 min on average and is idle for 5 h 30 min of it (Sadeghianpourhamami et al. 2018). Caltech is the right no-rule baseline because JPL drivers ran an informal move-after-charge program (Lee et al. 2019).
- **References:** Wolbertus et al. 2018; Sadeghianpourhamami et al. 2018; Lee et al. 2019
- **Check:** Policy rankings with rho = 0 and rho = baseline should agree; if not, report both.
- **Depends on:** Person B (dwell model)

## C. Fields that exist only for some sessions

### R09 · Requested energy for sessions without user input (未填写需求会话的“请求电量”) · P0

- **Needed for:** Primary output '% of requested energy delivered'; earliest-departure-first and equal-sharing schedulers
- **Why it is not in the data:** Only sessions claimed in the ACN app carry kWhRequested.
- **Evidence from ACN-Data:** User input exists for 40.3% of all sessions, but coverage is a regime effect: 5.6% before 2018-11-01 and 85.4% after, when weekday plug-ins also fell from 79 to 39 per day. The 1,473 later sessions without input delivered a median 0.90 kWh (90% at most 1.45 kWh) against 7.62 kWh with input: the footprint of the rule that cut unclaimed sessions off, not of real demand.
- **Synthesis method:** Calibrate request-dependent experiments on the post-2018-11 regime and EXCLUDE its unclaimed sessions from demand fitting (they are policy truncations). For sessions that still need a request, use a ratio hot-deck within day-type x arrival-period cells: requested = delivered / r, with r drawn from same-regime donors (EmpiricalInputErrors). Do not impute the pre-2018-11 regime from later donors. Report service against both requested energy and energy demand, because requests are inflated (R10).
- **Baseline / range:** Cell-wise ratio hot-deck (MAR) / MNAR multiplier 0.8-1.2 on imputed requests
- **Scientific basis:** A session carries user input only when the driver claims it in the ACN app; unclaimed sessions have no userID and ran on policy defaults, and from 2018-11-01 unclaimed Caltech sessions were terminated after 30 min (Lee et al. 2019; ACN-Data documentation). Hot-deck imputation within adjustment cells is valid under missing-at-random given the cell variables, provided every cell has donors (Andridge & Little 2010), which fails for sparse evening and weekend cells in the early regime; a multiplicative delta adjustment is the standard sensitivity check for missing-not-at-random (van Buuren 2018).
- **References:** Lee et al. 2019; ACN-Data documentation; Andridge & Little 2010; van Buuren 2018
- **Check:** Hold out 20% of sessions WITH input, impute them, and compare imputed vs actual requests by cell.
- **Depends on:** Person A (clean input fields, unclaimed flag)

### R10 · Announced departure (and request) error (用户报的离开时间/电量与实际的偏差) · P0

- **Needed for:** Earliest-departure-first scheduling uses the announced departure, not the true one
- **Why it is not in the data:** True departure is known after the fact; a scheduler only sees the announcement, which exists for a minority of sessions.
- **Evidence from ACN-Data:** First-version requests in the post_2018-11 regime: median announced minus actual departure = -43 min (IQR 192 min); 70% stayed longer than announced and only 26% left within +/-30 min. Delivered/requested energy median = 0.57; 79% of requests exceed delivery by more than 20%.
- **Synthesis method:** announced = actual + e, with e resampled by cell from first-version errors (EmpiricalInputErrors.announced_departure). Use the FIRST input version, which is what a scheduler sees at plug-in.
- **Baseline / range:** Empirical error resampling / Perfect information (e = 0); model-predicted inputs as an alternative
- **Scientific basis:** In ACN-Data, driver-entered departure and energy predicted actual behavior worse than a per-user mean or a Gaussian-mixture model: at Caltech, SMAPE 25.8 (duration) and 27.6 (energy) for user inputs versus 15.9 and 14.4 for the individual mixture model, on the paper's half-scale SMAPE (Lee et al. 2019). ACN-Sim separates actual from estimated departure so schedulers can run on noisy inputs (Lee et al. 2021, ACN-Sim). Model-based departure prediction is a third information scenario (Frendo et al. 2021).
- **References:** Lee et al. 2019; Lee et al. 2021 (ACN-Sim); Frendo et al. 2021
- **Check:** Run earliest-departure-first with e = 0 and with empirical e; the gap is the value of accurate driver inputs.
- **Depends on:** Person A (first-version inputs)

### R11 · True energy demand of sessions cut off by departure (被提前拔枪截断的真实电量需求) · P1

- **Needed for:** Energy demand input; '% energy delivered' and 'sessions fully served' outputs
- **Why it is not in the data:** When a driver unplugs mid-charge, kWhDelivered is only a lower bound on what the vehicle needed.
- **Evidence from ACN-Data:** 27.4% of sessions have completion recorded within 1 min before unplug; in a time-series sample of 100 of them, current was still flowing within 5 min of unplug in 100%. Another 4.2% have completion recorded AFTER unplug, and only 54% of a sample of 100 were still drawing current. Time-series-adjusted share still charging at unplug: 29.7% (unadjusted 38% before 2018-11-01, 24% after). Among these sessions with a request, 8% reached 95% of it.
- **Synthesis method:** Baseline: demand = delivered (conservative). Sensitivity: demand = delivered x u, with u from the requested/delivered ratio of completed sessions in the same cell, or fit the energy model as right-censored in Person B's joint model.
- **Baseline / range:** demand = delivered / Up to cell-wise inflation from completed sessions
- **Scientific basis:** The ACN scheduler caps delivered energy at the request, or for unclaimed sessions at a default (before 2018-09-01: 42, 21 or 14 kWh over 12 h by EVSE; 14 kWh from 2018-09-01) (Lee et al. 2019), and ACN-Sim's reference loader sets demand equal to kWhDelivered, so delivered energy is a lower bound whenever charging was cut off (acnportal v0.3.3). Censorship-aware (Tobit-type) models recover latent EV charging demand better than models that ignore censoring (Hüttel et al. 2023, aggregate-demand setting).
- **References:** Lee et al. 2019; acnportal v0.3.3; Hüttel et al. 2023
- **Check:** Compare the energy-demand distribution with and without the censoring adjustment.
- **Depends on:** Person B (energy model)

### R14 · Missing or late charging-completion time (缺失/记录偏晚的充电完成时间) · P2

- **Needed for:** Idle-time validation targets
- **Why it is not in the data:** doneChargingTime is blank for some sessions and recorded after unplugging for others.
- **Evidence from ACN-Data:** 2.0% of sessions lack doneChargingTime. Time-series sample: 77% of those stopped drawing current >5 min before unplug (median 93 min), and 46% of sessions whose completion was recorded after unplug had in fact finished earlier.
- **Synthesis method:** Recover completion as the last time-series sample with current >= 1 A; without time series, completion = connect + delivered / rate, with rate from completed sessions of the same month (clipped to unplug).
- **Baseline / range:** Time-series recovery / —
- **Scientific basis:** ACN-Data defines doneChargingTime as the time of the last non-zero current draw and publishes per-session current series (ACN-Data documentation).
- **References:** ACN-Data documentation
- **Check:** Session kWh matches the time-series cumulative energy (median absolute difference 0.0006 kWh).
- **Depends on:** Person A

## D. Vehicle and site parameters

### R12 · Vehicle maximum charging rate (车辆自身最大充电功率) · P0

- **Needed for:** Session schema field 'maximum charging rate'; every charging policy
- **Why it is not in the data:** The session table has no vehicle model or onboard-charger rating, and observed current is capped by the scheduler.
- **Evidence from ACN-Data:** Random sample of 299 sessions from 2018-11 on: in the 198 with periods where the scheduler allowed >= 30 A, the power drawn is bimodal: 52% of vehicles in a low class (median 3.27 kW, i.e. 16 A onboard chargers) and 48% in a high class (median 6.43 kW, at the 32 A x 208 V = 6.66 kW port limit), with 0% in between.
- **Synthesis method:** VehicleRateMix: two onboard-charger classes with the empirical probabilities, capped at the port rating; linear taper after the transition state of charge.
- **Baseline / range:** See scenario_parameters.csv (vehicle_kw_class_1/2); taper from SoC 0.8 / Class shares +/- 0.1; taper start 0.7-0.9
- **Scientific basis:** An EV draws the lower of the EVSE pilot and its onboard-charger limit; ACN-Sim models this plus a linear taper that starts at a state of charge of 0.7-0.9 (Lee et al. 2021, ACN-Sim). At a workplace with mixed EVSEs, Level-2 power was set by the vehicle's onboard charger, with groups near 2.0, 3.0-3.8 and above 6.0 kW (Francfort et al. 2014), and a Nissan LEAF drew about 3.3 kW on a 7.2 kW EVSE (Schey et al. 2012). Level 2 at commercial sites typically runs at 208 V (DOE AFDC). Taper shapes can be taken from clustered ACN charging tails (Sun et al. 2020).
- **References:** Lee et al. 2021 (ACN-Sim); Francfort et al. 2014 (INL); Schey et al. 2012; DOE AFDC; Sun et al. 2020
- **Check:** Compare simulated vs observed per-session peak power on the time-series sample.
- **Depends on:** Simulator (charging model)

### R13 · Uncontrolled-charging baseline (“不管控充电”基线轨迹) · P0

- **Needed for:** The 'uncontrolled charging' policy and a fair comparison of all policies
- **Why it is not in the data:** Caltech ran adaptive (managed) charging during collection, so observed charging durations and idle times are policy outputs, not inputs.
- **Evidence from ACN-Data:** In the median session of a random post-2018-11 sample, 38% of samples are in state ADAPTIVE and current is below the pilot (vehicle-limited) in only 14% of charging samples. Realized average charging power: median 3.0 kW against a 6.66 kW port rating.
- **Synthesis method:** Do not reuse observed charge durations or idle times as inputs. Regenerate charging from (arrival, dwell, energy demand, vehicle max rate, policy); uncontrolled = each vehicle draws min(vehicle max, port rating) from plug-in until demand is met.
- **Baseline / range:** Endogenous charging / —
- **Scientific basis:** ACN-Data was collected under an adaptive scheduler that caps each vehicle's energy at its request (Lee et al. 2019); the pilot is only an upper bound, and the site's MPC scheduler lowers a pilot toward the measured current when a vehicle draws less (Lee et al. 2021, ACN). ACN-Sim's uncontrolled baseline charges every EV at its maximum allowable rate and ignores infrastructure limits (Lee et al. 2021, ACN-Sim). The policy matters: with a 60 kW limit, an offline optimal schedule (perfect information, no network constraints) would have met Caltech demand on 100% of days but uncontrolled charging on only 22% (Lee et al. 2019).
- **References:** Lee et al. 2019; Lee et al. 2021 (ACN); Lee et al. 2021 (ACN-Sim)
- **Check:** Replay observed sessions under an adaptive scheduler and check completion times against doneChargingTime.
- **Depends on:** Simulator

### R15 · Site electrical limit and port rating (站点电力容量上限与单桩额定功率) · P0

- **Needed for:** Power-limit scenarios and peak-power output
- **Why it is not in the data:** Session records contain no infrastructure description.
- **Evidence from ACN-Data:** 54 stations observed; realized average charging power exceeded 6.656 kW (32 A x 208 V) in only 1.8% of sessions.
- **Synthesis method:** Use the acnportal Caltech network (54 ports, 208 V, 150 kVA transformer, 80 A pod limits, per-phase line limits) as the baseline, then sweep the site limit and port count.
- **Baseline / range:** 150 kVA site limit; 32 A x 208 V per port / Site limit 20-150 kW; ports 30/42/54/66
- **Scientific basis:** The Caltech garage's 54 EVSEs (6.7 kW each at 208 V) sit on a 150 kVA transformer, a 2.4x oversubscription, and two 8-EVSE pods share 80 A lines (Lee et al. 2021, ACN). acnportal's caltech_acn() encodes these limits: 208 V, a 150 transformer capacity, 80 A pods and per-phase primary and secondary line-current constraints (acnportal v0.3.3). Caltech's actual system capacity is 150 kW (Lee et al. 2019).
- **References:** Lee et al. 2021 (ACN); acnportal v0.3.3; Lee et al. 2019
- **Check:** Observed peak aggregate power must not exceed the modeled limit.
- **Depends on:** —

### R16 · Ports out of service, failed sessions and non-charging occupancy (故障接口、失败会话与被占用但不充电的车位) · P1

- **Needed for:** Effective capacity in every scenario
- **Why it is not in the data:** Outages are not logged in session data, near-zero-energy sessions are not in the records, and a vehicle parked without plugging in leaves no trace.
- **Evidence from ACN-Data:** Stations with sessions per month ranged 42-54 of 54; 109 runs of >= 10 weekdays with no session cover 16% of station-weekdays (outage or disuse; session data cannot tell which). The smallest recorded session is 0.50 kWh, so failed near-zero sessions are absent from the data.
- **Synthesis method:** PortAvailability: per-port two-state chain with long-run availability A and mean outage length; binomial thinning for spaces blocked by non-charging vehicles; Bernoulli session failure (occupies the port, delivers ~0 kWh).
- **Baseline / range:** A = 0.97; outage 7 days (assumption); blocked space 1%; session failure 0 / A 0.73-1.0; outage 1-30 days; blocked 0-9%; failure 0-3%
- **Scientific basis:** A field audit of public DC fast chargers found 73.3% of 655 ports functional in the published version (72.5% of 657 in the preprint, counting ports occupied by a charging EV as functional) (Rempel et al. 2024), a pessimistic analogue for a workplace Level-2 garage. Federal rules require publicly funded chargers to exceed 97% annual uptime per port, a floor that does not bind a private workplace garage (23 CFR 680.116). In 10.7 million public Level-2 sessions, 2.9% delivered almost no energy (<0.3 kWh), and 97% of those had no abnormal stop code (EV WATTS). In a snapshot of on-street charge points in Berlin, 1% were blocked by a non-charging vehicle where two or more legal parking spots were nearby and 34% where none were (Guillet et al. 2022, TomTom data).
- **References:** Rempel et al. 2024; 23 CFR 680.116; EV WATTS; Guillet et al. 2022
- **Check:** Ask U-M LTP for maintenance logs; otherwise report results at A = 1.0 and A = 0.9.
- **Depends on:** —

## E. Scenario generation and cost

### R17 · Demand growth scenarios (80-150%) (80%–150% 需求情景的生成) · P0

- **Needed for:** Demand-intensity axis of the experiment
- **Why it is not in the data:** Only one demand level is observed per regime.
- **Evidence from ACN-Data:** Weekday plug-ins per day: 78.7 before 2018-11-01 vs 38.6 after (variance/mean 2.5 and 3.1; 9.8 if the regimes are pooled; Poisson would be 1). A 100% demand level is only meaningful within one regime.
- **Synthesis method:** Fix one regime as the 100% reference (pre-2018-11 is the natural high-demand stress case, post-2018-11 the paid-charging case). scale_demand: independent thinning for factors < 1; for factors > 1, per-day bootstrap copies with jittered times, which scales each day's rate multiplicatively and keeps day-to-day overdispersion.
- **Baseline / range:** 1.0 / 0.8, 1.0, 1.2, 1.5
- **Scientific basis:** Thinning is the exact way to simulate a nonhomogeneous Poisson process at a lower rate (Lewis & Shedler 1979). Service arrivals are often Poisson within a day but overdispersed across days, which calls for a random daily rate (Kim & Whitt 2014); Gamma mixing gives a negative binomial daily count (Mathijsen et al. 2018), and negative binomial counts fit a validated EV session generator best (Lahariya et al. 2020). The ACN group built scenarios from a daily count plus sampled sessions (Lee et al. 2019) and scaled demand through sessions per day (Lee et al. 2021, ACN-Sim).
- **References:** Lewis & Shedler 1979; Kim & Whitt 2014; Mathijsen et al. 2018; Lahariya et al. 2020; Lee et al. 2019; Lee et al. 2021 (ACN-Sim)
- **Check:** Check that simulated daily counts at factor 1 reproduce the observed variance/mean within the chosen regime.
- **Depends on:** Person B (arrival model)

### R18 · Capital and operating cost parameters (投资与运营成本参数) · P1

- **Needed for:** 'Estimated capital and operating cost' output; minimum-cost intervention
- **Why it is not in the data:** ACN-Data has no cost or tariff information.
- **Evidence from ACN-Data:** Not in the data.
- **Synthesis method:** Annualized cost = (port hardware + installation) / life + network fee + demand charge on monthly peak kW + energy by time of use; an electrical-service upgrade term applies when the site limit is raised.
- **Baseline / range:** CAPEX about $5,500/port (2019 $); demand charge $15.51/kW-month; life 10 yr / CAPEX $3,400-7,300/port; demand $3-40/kW-month; network fee $100-900/port-yr
- **Scientific basis:** ICCT gives 2019 per-charger Level-2 hardware ($1,182 non-networked to $3,127 networked, single) and California installation ($2,837 per charger at sites with 6+ chargers; workplaces about 10% lower) (Nicholas 2019). DOE reports installation from $600 to $12,700 per unit, EV Project workplace installs averaging $2,223, demand charges of $3-$40/kW on the monthly peak, network fees of $100-$900 per year, and that some stakeholders assume a life of at least 10 years (Smith & Castellano 2015). The ACN operating-cost study used SCE TOU-EV-4 with a $15.51/kW-month demand charge (Lee et al. 2021, ACN). No transformer or service-upgrade cost for a Level-2 garage was found: ICCT folds a small utility-upgrade cost into Level-2 installation materials, and DOE's figures ($10,000-$25,000 for a transformer, $3,500-$9,500 for new service) come from DC fast-charger sites. Dollar years differ across sources.
- **References:** Nicholas 2019 (ICCT); Smith & Castellano 2015; Lee et al. 2021 (ACN)
- **Check:** Report which cost term drives the choice between adding ports and adding kW.
- **Depends on:** Person D (literature)

## References

- Andridge, R. R., & Little, R. J. A. (2010). A review of hot deck imputation for survey non-response. International Statistical Review, 78(1), 40-64. https://doi.org/10.1111/j.1751-5823.2010.00103.x
- Asensio, O. I., Apablaza, C. Z., Lawson, M. C., & Walsh, S. E. (2022). A field experiment on workplace norms and electric vehicle charging etiquette. Journal of Industrial Ecology, 26(1), 183-196. https://doi.org/10.1111/jiec.13116 *(effect sizes not yet checked in the full text; cited without numbers)*
- Aveklouris, A., Nakahira, Y., Vlasiou, M., & Zwart, B. (2017). Electric vehicle charging: A queueing approach. ACM SIGMETRICS Performance Evaluation Review, 45(2), 33-35. https://doi.org/10.1145/3152042.3152054
- Bae, S., Zeng, T., Travacca, B., & Moura, S. (2020). Inducing human behavior to alleviate overstay at PEV charging station. In 2020 American Control Conference (ACC) (pp. 2388-2394). https://doi.org/10.23919/ACC45564.2020.9147587 *(its behavior-model parameters are synthetic; only the observed idle share is cited)*
- Biswas, A., Gopalakrishnan, R., & Dutta, P. (2016). Managing overstaying electric vehicles in park-and-charge facilities. In Proceedings of the 25th International Joint Conference on Artificial Intelligence (IJCAI-16). arXiv:1604.05471. https://arxiv.org/abs/1604.05471 *(its penalty-tolerance distribution is assumed, not estimated)*
- Brown, L., Gans, N., Mandelbaum, A., Sakov, A., Shen, H., Zeltyn, S., & Zhao, L. (2005). Statistical analysis of a telephone call center: A queueing-science perspective. Journal of the American Statistical Association, 100(469), 36-50. https://doi.org/10.1198/016214504000001808 *(method only; its patience values are call-center specific)*
- California Polytechnic State University, San Luis Obispo, Transportation & Parking Services. (n.d.). Electric vehicles. Retrieved September 30, 2026. https://afd.calpoly.edu/parking/commutingtocampus/electricvehicles *(operator practice)*
- Caltech Adaptive Charging Network. (n.d.). ACN-Data: A public EV charging dataset (field definitions and API documentation). Retrieved September 30, 2026. https://ev.caltech.edu/dataset *(dataset documentation)*
- ChargePoint, Inc. (n.d.). Waitlist driver FAQ. Retrieved September 30, 2026. https://na.chargepoint.com/waitlist_driver_faq *(industry practice)*
- Federal Highway Administration. (2023). National electric vehicle infrastructure standards and requirements, 23 C.F.R. § 680.116(b) (Minimum uptime). 88 Fed. Reg. 12724. https://www.law.cornell.edu/cfr/text/23/680.116 *(regulatory floor for publicly funded chargers, not a measurement)*
- Francfort, J., Smart, J., & Scoffield, D. (2014, July 21). Workplace charging case study: Charging station utilization at a work site with AC Level 1, AC Level 2, and DC fast charging units [Presentation to the WA State EV Working Group] (INL/MIS-14-32498). Idaho National Laboratory. https://web.archive.org/web/20210318010409/https://avt.inl.gov/sites/default/files/pdf/presentations/WorkplaceChargingSeattlePEVworkinggroupJuly2014.pdf *(slide presentation; same site and data as INL 2014)*
- Frendo, O., Gaertner, N., & Stuckenschmidt, H. (2021). Improving smart charging prioritization by predicting electric vehicle departure time. IEEE Transactions on Intelligent Transportation Systems, 22(10), 6646-6653. https://doi.org/10.1109/TITS.2020.2988648 *(cited without numbers)*
- Garg, T., Hanna, R., Myers, J., Tebbe, S., & Victor, D. G. (2024). Electric vehicle charging at the workplace: Experimental evidence on incentives and environmental nudges (CESifo Working Paper No. 11445). https://www.ifo.de/DocDL/cesifo1_wp11445.pdf *(numbers cited from the working paper)*
- Garg, T., Hanna, R., Myers, J., Tebbe, S., & Victor, D. G. (2026). Electric vehicle charging at the workplace: Experimental evidence on incentives and environmental nudges. Journal of Environmental Economics and Management, 139, 103383. https://doi.org/10.1016/j.jeem.2026.103383 *(published version; cited for direction only)*
- Guillet, M., Hiermann, G., Kröller, A., & Schiffer, M. (2022). Electric vehicle charging station search in stochastic environments. Transportation Science, 56(2), 483-500. https://doi.org/10.1287/trsc.2021.1102 *(blocking shares come from an internal TomTom study of on-street charge points in Berlin; weakly transferable to a garage)*
- Guo, P., Xiao, B., & Li, J. (2012). Unconstraining methods in revenue management systems: Research overview and prospects. Advances in Operations Research, 2012, 270910. https://doi.org/10.1155/2012/270910
- Hüttel, F. B., Rodrigues, F., & Pereira, F. C. (2023). Mind the gap: Modelling difference between censored and uncensored electric vehicle charging demand. Transportation Research Part C: Emerging Technologies, 153, 104189. https://doi.org/10.1016/j.trc.2023.104189 *(aggregate demand in a simulated Copenhagen setting; cited for the method, not its numbers)*
- Idaho National Laboratory. (2014, June). Workplace charging case study: Charging station utilization at a work site with AC Level 1, AC Level 2, and DC fast charging units (INL/EXT-14-32340). The EV Project and ChargePoint America, U.S. Department of Energy. https://portal.ct.gov/-/media/deep/air/mobile/evconnecticut/evprojectworkplaceevseutilizationatfacebookjun2014pdf.pdf *(corporate author; site: Facebook campus, Menlo Park, CA)*
- Kim, S.-H., & Whitt, W. (2014). Are call center and hospital arrivals well modeled by nonhomogeneous Poisson processes? Manufacturing & Service Operations Management, 16(3), 464-480. https://doi.org/10.1287/msom.2014.0490 *(abstract read)*
- Lahariya, M., Benoit, D. F., & Develder, C. (2020). Synthetic data generator for electric vehicle charging sessions: Modeling and evaluation using real-world data. Energies, 13(16), 4211. https://doi.org/10.3390/en13164211
- Lee, Z. J., Lee, G., Lee, T., Jin, C., Lee, R., Low, Z., Chang, D., Ortega, C., & Low, S. H. (2021). Adaptive charging networks: A framework for smart electric vehicle charging. IEEE Transactions on Smart Grid, 12(5), 4339-4350. https://doi.org/10.1109/TSG.2021.3074437 *(numbers checked against arXiv:2012.02636)*
- Lee, Z. J., Li, T., & Low, S. H. (2019). ACN-Data: Analysis and applications of an open EV charging dataset. In Proceedings of the Tenth ACM International Conference on Future Energy Systems (e-Energy '19) (pp. 139-149). https://doi.org/10.1145/3307772.3328313
- Lee, Z. J., Sharma, S., & Johansson, D. (2023). acnportal (Version 0.3.3) [Computer software]. GitHub zach401/acnportal, commit 14e723b. https://github.com/zach401/acnportal
- Lee, Z. J., Sharma, S., Johansson, D., & Low, S. H. (2021). ACN-Sim: An open-source simulator for data-driven electric vehicle charging research. IEEE Transactions on Smart Grid, 12(6), 5113-5123. https://doi.org/10.1109/TSG.2021.3103156 *(numbers checked against arXiv:2012.02809)*
- Lewis, P. A. W., & Shedler, G. S. (1979). Simulation of nonhomogeneous Poisson processes by thinning. Naval Research Logistics Quarterly, 26(3), 403-413. https://doi.org/10.1002/nav.3800260304
- Mathijsen, B. W. J., Janssen, A. J. E. M., van Leeuwaarden, J. S. H., & Zwart, B. (2018). Robust heavy-traffic approximations for service systems facing overdispersed demand. Queueing Systems, 90(3-4), 257-289. https://doi.org/10.1007/s11134-018-9584-z
- Nicholas, M. (2019). Estimating electric vehicle charging infrastructure costs across major U.S. metropolitan areas (Working Paper 2019-14). International Council on Clean Transportation. https://theicct.org/wp-content/uploads/2021/06/ICCT_EV_Charging_Cost_20190813.pdf
- Nicholas, M. A., & Tal, G. (2013). Charging for charging at work: Increasing the availability of charging through pricing (Working Paper UCD-ITS-WP-13-02). Institute of Transportation Studies, University of California, Davis. https://itspubs.ucdavis.edu/download_pdf.php?id=1919 *(working paper, not peer reviewed)*
- Rempel, D., Cullen, C., Matteson Bryan, M., & Vianna Cezar, G. (2024). Reliability of open public electric vehicle direct current fast chargers. Human Factors, 66(11), 2528-2538. https://doi.org/10.1177/00187208231215242 *(72.5% is from the preprint arXiv:2203.16372 and includes ports assumed functional because a charging EV occupied them; the published version reports 73.3% of 655 ports)*
- Sadeghianpourhamami, N., Refa, N., Strobbe, M., & Develder, C. (2018). Quantitive analysis of electric vehicle flexibility: A data-driven approach. International Journal of Electrical Power & Energy Systems, 95, 451-462. https://doi.org/10.1016/j.ijepes.2017.09.007
- Schey, S., Scoffield, D., & Smart, J. (2012). A first look at the impact of electric vehicle charging on the electric grid in the EV Project. World Electric Vehicle Journal, 5(3), 667-678. https://doi.org/10.3390/wevj5030667
- Smith, M., & Castellano, J. (2015). Costs associated with non-residential electric vehicle supply equipment: Factors to consider in the implementation of electric vehicle charging stations. New West Technologies, LLC, for the U.S. Department of Energy Vehicle Technologies Office. https://afdc.energy.gov/uploads/publication/evse_cost_report_2015.pdf *(2011-2015 costs; use as bounds)*
- Sun, C., Li, T., Low, S. H., & Li, V. O. K. (2020). Classification of electric vehicle charging time series with selective clustering. Electric Power Systems Research, 189, 106695. https://doi.org/10.1016/j.epsr.2020.106695 *(taper clusters come from a small JPL sample)*
- Sutton, K. M., Hardman, S., & Tal, G. (2022). Strategies to reduce congestion and increase access to electric vehicle charging stations at workplaces (Research Report UCD-ITS-RR-22-55). Institute of Transportation Studies, University of California, Davis. https://escholarship.org/uc/item/2345r48k *(interview study (40 drivers), not peer reviewed)*
- U.S. Department of Energy, Alternative Fuels Data Center. (n.d.). Electric vehicle charging stations. Retrieved September 30, 2026. https://afdc.energy.gov/fuels/electricity-stations
- VanSlyke, M., Ichien, D., Ng, J., & Heidel, W. (c. 2024). Electric vehicle charging station reliability (EV WATTS Whitepaper Series). Energetics, supported by the U.S. DOE Vehicle Technologies Office. https://clearesult.com/sites/default/files/2024-05/EV-WATTS_White-Paper_Charging-Station-Reliability.pdf *(public networked chargers)*
- Wolbertus, R., & Gerzon, B. (2018). Improving electric vehicle charging station efficiency through pricing. Journal of Advanced Transportation, 2018, 4831951. https://doi.org/10.1155/2018/4831951 *(abstract read; cited for design and direction only)*
- Wolbertus, R., Kroesen, M., van den Hoed, R., & Chorus, C. (2018). Fully charged: An empirical study into the factors that influence connection times at EV-charging stations. Energy Policy, 123, 1-7. https://doi.org/10.1016/j.enpol.2018.08.030
- Zeng, T., Zhang, H., & Moura, S. (2019). Solving overstay in PEV charging station planning via chance constrained optimization. arXiv:1901.07110. https://arxiv.org/abs/1901.07110 *(related journal paper: Zeng, Zhang & Moura (2020), IEEE Trans. Industrial Informatics 16(5), 3504-3514; figures cited from the preprint)*
- van Buuren, S. (2018). Flexible imputation of missing data (2nd ed.). Chapman & Hall/CRC. https://stefvanbuuren.name/fimd/
