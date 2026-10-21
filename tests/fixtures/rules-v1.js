const row = $input.first().json;
const users = typeof row.users === 'string' ? JSON.parse(row.users) : (row.users || []);
if (Number(row.product_count) < 1 || Number(row.product_count) > 400) throw new Error('Run needs 1 to 400 pinned products');
const results = [];
for (const user of users) {
  for (const product of (user.products || [])) {
    const rules = typeof product.rules === 'string' ? JSON.parse(product.rules) : product.rules;
    const reasons = [];
    let hardFailure = false;
    let unresolved = false;
    const add = (criterion,status,extra={}) => reasons.push({criterion,status,...extra});
    const employment = rules.employment || {};
    if (employment.kind === 'required') {
      const pass = user.employment_status === employment.status;
      add('employment_status',pass?'pass':'fail',{required_value:employment.status,observed_value:user.employment_status});
      if (!pass) hardFailure = true;
    } else { unresolved = true; add('employment_status','unknown',{message:'This product employment rule needs confirmation.'}); }
    const age = rules.age || {};
    if (age.kind === 'required') {
      const pass = Number.isInteger(Number(user.age)) && Number(user.age) >= age.min && Number(user.age) <= age.max;
      add('age',pass?'pass':'fail',{minimum:age.min,maximum:age.max,observed_value:user.age});
      if (!pass) hardFailure = true;
    } else { unresolved = true; add('age','unknown',{message:'The official source does not establish the age criteria.'}); }
    const income = rules.monthly_income || {};
    if (income.kind === 'required' && !income.location_dependent) {
      const value = Number(user.monthly_income);
      const pass = Number.isFinite(value) && value >= income.min;
      add('monthly_income',pass?'pass':'fail',{minimum:income.min,currency:income.currency||'INR',observed_value:user.monthly_income});
      if (!pass) hardFailure = true;
    } else { unresolved = true; add('monthly_income','unknown',{message:'Location-specific income terms cannot be checked from this file.'}); }
    const score = rules.credit_score || {};
    if (score.kind === 'required') {
      const pass = Number.isInteger(Number(user.credit_score)) && Number(user.credit_score) >= score.min;
      add('credit_score',pass?'pass':'fail',{minimum:score.min,observed_value:user.credit_score});
      if (!pass) hardFailure = true;
    } else if (score.kind === 'preferred') {
      const pass = Number.isInteger(Number(user.credit_score)) && Number(user.credit_score) >= score.min;
      add('credit_score',pass?'pass':'review',{preferred_minimum:score.min,observed_value:user.credit_score,message:'The source describes this as preferred, not a hard cutoff.'});
      if (!pass) unresolved = true;
    } else { unresolved = true; add('credit_score','unknown',{message:'The official source does not establish a score cutoff.'}); }
    results.push({user_id:user.user_id,product_id:product.product_id,snapshot_id:product.snapshot_id,decision:hardFailure?'ineligible':unresolved?'review':'eligible',reasons,rule_version:'rules-v1'});
  }
}
return [{json:{run_id:row.run_id,cursor_user_id:row.cursor_user_id,results,last_user_id:row.last_user_id,has_more:row.has_more}}];