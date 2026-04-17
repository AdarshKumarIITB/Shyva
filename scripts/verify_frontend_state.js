const fs = require('fs');
const path = require('path');
const vm = require('vm');

function makeClassList(initial = ['hidden']) {
  const classes = new Set(initial);
  return {
    add(name) { classes.add(name); },
    remove(name) { classes.delete(name); },
    contains(name) { return classes.has(name); },
    toArray() { return Array.from(classes); },
  };
}

function makeElement(selector) {
  return {
    selector,
    innerHTML: '',
    textContent: '',
    value: '',
    disabled: false,
    children: [],
    className: '',
    classList: makeClassList(),
    addEventListener() {},
    appendChild(child) { this.children.push(child); },
    querySelector() { return makeElement('child'); },
  };
}

const elements = new Map();
[
  '#classify-form',
  '#description',
  '#origin',
  '#destination',
  '#classify-btn',
  '#questions-section',
  '#questions-container',
  '#classification-section',
  '#classification-result',
  '#duty-section',
  '#duty-result',
  '#audit-section',
  '#audit-result',
].forEach((selector) => {
  elements.set(selector, makeElement(selector));
});

elements.get('#origin').value = 'CN';
elements.get('#destination').value = 'US';

const document = {
  querySelector(selector) {
    if (!elements.has(selector)) {
      elements.set(selector, makeElement(selector));
    }
    return elements.get(selector);
  },
  createElement(tag) {
    const element = makeElement(tag);
    if (tag === 'div') {
      const optionsDiv = makeElement('.options');
      element.querySelector = (selector) => (selector === '.options' ? optionsDiv : makeElement(selector));
    }
    return element;
  },
};

const context = {
  console,
  document,
  fetch: async () => ({ json: async () => ({}) }),
  alert: () => {},
};

const appCode = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'app.js'), 'utf8');
vm.runInNewContext(appCode, context, { filename: 'app.js' });

function assertTrue(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

context.handleResponse({
  status: 'duties_resolved',
  classification: {
    primary_code: {
      hs6: '8534.00',
      national_code: '8534.00.0000',
      confidence: 'medium',
      description: 'Printed circuit boards',
      reasoning: 'heading=8534 -> hs6=8534.00 -> national_code=8534.00.0000',
      legal_basis: ['Validation reasoning'],
      warnings: ['Classification is conditional on recorded assumptions.'],
    },
    alternative_codes: [
      {
        hs6: '8534.00',
        national_code: '8534.00.1000',
        reasoning: 'Alternative retained for validation.',
      },
    ],
    conditional: true,
    assumption_summary: [
      'bare_or_populated: assumed bare; alternatives retained: populated.',
      '_candidate_code: assumed 8534.00.0000; alternatives retained: 8534.00.1000.',
    ],
    locked_levels: [
      {
        level: 'heading',
        value: '8534',
        facts_used: ['product_family', 'bare_or_populated'],
        alternatives_rejected: [],
      },
      {
        level: 'hs6',
        value: '8534.00',
        facts_used: ['product_family', 'bare_or_populated'],
        alternatives_rejected: [],
      },
      {
        level: 'national_code',
        value: '8534.00.0000',
        facts_used: ['product_family', 'bare_or_populated'],
        alternatives_rejected: ['8534.00.1000'],
      },
    ],
    candidate_summary: [
      {
        code: '8534.00.0000',
        level: 'national',
        status: 'selected',
        reasoning: 'Selected candidate.',
      },
      {
        code: '8534.00.1000',
        level: 'national',
        status: 'rejected',
        reasoning: 'Rejected alternative.',
      },
    ],
  },
  duty_stack: {
    effective_date_used: '2026-02-01',
    layers: [
      {
        measure_type: 'MFN',
        rate_value: '2.5%',
        applies_because: 'Synthetic validation duty',
      },
    ],
    total_ad_valorem_estimate: '2.5%',
    conditional_basis: ['Duty analysis is conditional on recorded assumptions.'],
    unresolved_measures: ['No trade remedies validated in frontend test.'],
    source_versions: ['validation-suite-v1'],
    notes: ['Origin CN into US'],
    warnings: ['Duty analysis is conditional on the recorded assumptions.'],
  },
  audit_trail: {
    user_input: 'generic printed circuit board',
    effective_date: '2026-02-01',
    assumptions: ['bare_or_populated: assumed bare; alternatives retained: populated.'],
    locked_digits: ['heading:8534', 'hs6:8534.00', 'national_code:8534.00.0000'],
    codes_considered: ['8534.00.0000', '8534.00.1000'],
    codes_rejected: ['8534.00.1000'],
    steps: [
      { step: 'phase_2_heading_locked', detail: 'Heading locked: 8534' },
      { step: 'phase_4_national_locked', detail: 'National code locked: 8534.00.0000' },
    ],
  },
});

assertTrue(!elements.get('#classification-section').classList.contains('hidden'), 'Classification section should be visible after rendering a result.');
assertTrue(elements.get('#classification-result').innerHTML.includes('Conditional result'), 'Classification output should surface conditional status.');
assertTrue(elements.get('#classification-result').innerHTML.includes('Locked Resolution Stages'), 'Classification output should surface locked stages.');
assertTrue(elements.get('#classification-result').innerHTML.includes('Candidate Path Summary'), 'Classification output should surface candidate summaries.');
assertTrue(!elements.get('#duty-section').classList.contains('hidden'), 'Duty section should be visible after rendering a result.');
assertTrue(elements.get('#duty-result').innerHTML.includes('Effective date used'), 'Duty output should surface the effective date provenance.');
assertTrue(elements.get('#duty-result').innerHTML.includes('validation-suite-v1'), 'Duty output should surface source versions.');
assertTrue(!elements.get('#audit-section').classList.contains('hidden'), 'Audit section should be visible after rendering a result.');
assertTrue(elements.get('#audit-result').innerHTML.includes('Locked digits'), 'Audit output should surface locked digits.');
assertTrue(elements.get('#audit-result').innerHTML.includes('Codes rejected'), 'Audit output should surface rejected codes.');

context.handleResponse({
  status: 'clarifying',
  pending_questions: [
    {
      question: 'Is this a bare circuit board with no components mounted, or a populated board with components mounted on it?',
      fact_key: 'bare_or_populated',
      options: ['bare', 'populated'],
      legal_context: 'This detail is required to lock the next set of tariff digits.',
    },
  ],
  classification: null,
  duty_stack: null,
  audit_trail: null,
});

assertTrue(elements.get('#classification-section').classList.contains('hidden'), 'Classification section should be hidden when the latest response has no classification.');
assertTrue(elements.get('#classification-result').innerHTML === '', 'Classification markup should be cleared when classification is absent.');
assertTrue(elements.get('#duty-section').classList.contains('hidden'), 'Duty section should be hidden when the latest response has no duty stack.');
assertTrue(elements.get('#audit-section').classList.contains('hidden'), 'Audit section should be hidden when the latest response has no audit trail.');
assertTrue(!elements.get('#questions-section').classList.contains('hidden'), 'Questions section should remain visible during clarification.');

console.log('Frontend validation passed.');
