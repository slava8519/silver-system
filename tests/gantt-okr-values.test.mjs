import assert from "node:assert/strict";
import test from "node:test";
import {checks, checkSummary, contributionPoints, fractionPercent, reading, readiness, threshold} from "../second_brain/web/gantt-okr-values.js";

test("independent views of outcome, criterion fulfilment and weighted work completion", () => {
  const kr = {unit:"мин", target:"15", measurement:{value:"0"}, progress:{value:"80"}, criteria:[
    {primary:true, unit:"мин", operator:"<=", target:"15", measurement:{value:"0"}, met:true},
    {kind:"boolean", met:null}
  ]};
  assert.equal(reading(kr),"0 мин");
  assert.equal(threshold(kr),"≤ 15 мин");
  assert.equal(checks(kr),"1 из 2");
  assert.equal(readiness(kr.progress),"80%");
  assert.equal(readiness({value:"70.6"}),"71%");
  assert.equal(readiness({value:null}),"—");
});

test("initiative contribution displays the exact reported points, including zero, independently of the total", () => {
  const progress = {
    value: null,
    contributions: {precise:"0.123456789012345678", zeroWeight:"0", unknownWeight:null, unknownReadiness:null}
  };
  assert.equal(contributionPoints(progress, "precise"), "0,123456789012345678 п. п.");
  assert.equal(contributionPoints(progress, "zeroWeight"), "0 п. п.");
  for (const id of ["unknownWeight", "unknownReadiness", "missingTask"]) {
    assert.equal(contributionPoints(progress, id), "—");
  }
  assert.equal(contributionPoints({}, "oldSnapshot"), "—");
});

test("legacy target preserves its unknown comparison and a cleared threshold stays empty", () => {
  assert.equal(threshold({unit:"кг",target:"4.2"}),"4,2 кг");
  assert.equal(threshold({criteria:[{primary:true,unit:"кг",operator:"<=",target:null}]}),"—");
  assert.equal(fractionPercent(null),"—");
  assert.match(fractionPercent("0"),/^0\s*%$/);
});

test("baseline alone never counts as a check", () => {
  const criteria = [{baseline:{value:"17"},measurement:null,met:null}];
  assert.equal(checkSummary({criteria}),"Нет проверок");
  criteria.push({measurement:{value:true},met:true});
  assert.equal(checkSummary({criteria}),"Выполнено 1 из 2");
  assert.equal(checkSummary({criteria:[{measurement:{value:"17"},met:null}]}),"Проверки без подтверждения");
});
