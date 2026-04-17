const API = '';  // same origin
let currentSessionId = null;

const $ = (sel) => document.querySelector(sel);
const show = (sel) => $(sel).classList.remove('hidden');
const hide = (sel) => $(sel).classList.add('hidden');

// --- Form submit ---
$('#classify-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const desc = $('#description').value.trim();
    if (!desc) return;

    resetResults();
    $('#classify-btn').disabled = true;
    $('#classify-btn').textContent = 'Classifying...';

    try {
        const res = await fetch(`${API}/api/classify`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                description: desc,
                origin: $('#origin').value,
                destination: $('#destination').value,
            }),
        });
        const data = await res.json();
        currentSessionId = data.session_id;
        handleResponse(data);
    } catch (err) {
        alert('Error: ' + err.message);
    } finally {
        $('#classify-btn').disabled = false;
        $('#classify-btn').textContent = 'Classify';
    }
});

function resetResults() {
    hide('#questions-section');
    hide('#classification-section');
    hide('#duty-section');
    hide('#audit-section');
    $('#questions-container').innerHTML = '';
    $('#classification-result').innerHTML = '';
    $('#duty-result').innerHTML = '';
    $('#audit-result').innerHTML = '';
}

function handleResponse(data) {
    if (data.status === 'clarifying' && data.pending_questions?.length) {
        renderQuestions(data.pending_questions);
    } else {
        hide('#questions-section');
        $('#questions-container').innerHTML = '';
    }

    if (data.classification) {
        renderClassification(data.classification);
    } else {
        hide('#classification-section');
        $('#classification-result').innerHTML = '';
    }

    if (data.duty_stack) {
        renderDutyStack(data.duty_stack);
    } else {
        hide('#duty-section');
        $('#duty-result').innerHTML = '';
    }

    if (data.audit_trail) {
        renderAudit(data.audit_trail);
    } else {
        hide('#audit-section');
        $('#audit-result').innerHTML = '';
    }
}

// --- Questions ---
function renderQuestions(questions) {
    const container = $('#questions-container');
    container.innerHTML = '';
    show('#questions-section');

    questions.forEach(q => {
        const card = document.createElement('div');
        card.className = 'question-card';
        card.innerHTML = `
            <p><strong>${q.question}</strong></p>
            ${q.legal_context ? `<p class="legal-context">${q.legal_context}</p>` : ''}
            <div class="options" data-fact="${q.fact_key}"></div>
        `;

        const optionsDiv = card.querySelector('.options');
        (q.options || []).forEach(opt => {
            const btn = document.createElement('button');
            btn.className = 'secondary';
            btn.textContent = opt;
            btn.addEventListener('click', () => submitAnswer(q.fact_key, opt, btn));
            optionsDiv.appendChild(btn);
        });

        container.appendChild(card);
    });
}

async function submitAnswer(factKey, value, btn) {
    // Visual feedback
    btn.parentElement.querySelectorAll('button').forEach(b => b.classList.remove('selected'));
    btn.classList.add('selected');
    btn.disabled = true;

    try {
        const res = await fetch(`${API}/api/clarify`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: currentSessionId,
                answers: { [factKey]: value },
            }),
        });
        const data = await res.json();
        handleResponse(data);
    } catch (err) {
        alert('Error: ' + err.message);
    }
}

// --- Classification ---
function renderClassification(cls) {
    show('#classification-section');
    const pc = cls.primary_code;
    if (!pc) {
        $('#classification-result').innerHTML = '<p>No classification result.</p>';
        return;
    }

    let html = `
        <div style="margin-bottom:12px">
            <span class="code-badge">${pc.national_code || pc.hs6}</span>
            <span class="confidence-${pc.confidence}" style="margin-left:12px; font-weight:600">
                Confidence: ${pc.confidence}
            </span>
            ${cls.conditional ? '<span class="warning" style="display:inline-block; margin-left:12px">Conditional result</span>' : ''}
        </div>
        <p><strong>HS-6:</strong> ${pc.hs6 || '—'}</p>
        <p><strong>Description:</strong> ${pc.description || '—'}</p>
        <p style="margin-top:8px"><strong>Reasoning:</strong> ${pc.reasoning}</p>
    `;

    if (pc.legal_basis?.length) {
        html += '<h3 style="margin-top:16px">Legal Basis</h3><ul>';
        pc.legal_basis.forEach(item => {
            html += `<li>${item}</li>`;
        });
        html += '</ul>';
    }

    if (cls.locked_levels?.length) {
        html += '<h3 style="margin-top:16px">Locked Resolution Stages</h3>';
        html += '<table class="duty-table"><thead><tr><th>Stage</th><th>Value</th><th>Facts Used</th><th>Rejected Alternatives</th></tr></thead><tbody>';
        cls.locked_levels.forEach(lock => {
            html += `<tr>
                <td>${lock.level}</td>
                <td><span class="code-badge" style="font-size:0.9em">${lock.value}</span></td>
                <td>${(lock.facts_used || []).join(', ') || '—'}</td>
                <td>${(lock.alternatives_rejected || []).join(', ') || '—'}</td>
            </tr>`;
        });
        html += '</tbody></table>';
    }

    if (cls.assumption_summary?.length) {
        html += '<h3 style="margin-top:16px">Assumptions</h3>';
        cls.assumption_summary.forEach(item => {
            html += `<div class="warning">${item}</div>`;
        });
    }

    if (cls.candidate_summary?.length) {
        html += '<h3 style="margin-top:16px">Candidate Path Summary</h3>';
        html += '<table class="duty-table"><thead><tr><th>Code</th><th>Level</th><th>Status</th><th>Reasoning</th></tr></thead><tbody>';
        cls.candidate_summary.forEach(candidate => {
            html += `<tr>
                <td><span class="code-badge" style="font-size:0.9em">${candidate.code}</span></td>
                <td>${candidate.level}</td>
                <td>${candidate.status}</td>
                <td>${candidate.reasoning}</td>
            </tr>`;
        });
        html += '</tbody></table>';
    }

    if (pc.warnings?.length) {
        html += pc.warnings.map(w => `<div class="warning">${w}</div>`).join('');
    }

    if (cls.alternative_codes?.length) {
        html += '<h3 style="margin-top:16px">Alternative Codes</h3>';
        cls.alternative_codes.forEach(alt => {
            html += `<p><span class="code-badge" style="font-size:0.9em">${alt.national_code || alt.hs6}</span> — ${alt.reasoning}</p>`;
        });
    }

    $('#classification-result').innerHTML = html;
}

// --- Duty Stack ---
function renderDutyStack(stack) {
    show('#duty-section');
    let html = '';

    if (stack.effective_date_used) {
        html += `<div class="note"><strong>Effective date used:</strong> ${stack.effective_date_used}</div>`;
    }

    html += '<table class="duty-table"><thead><tr><th>Measure</th><th>Rate</th><th>Basis</th></tr></thead><tbody>';

    (stack.layers || []).forEach(l => {
        html += `<tr>
            <td>${formatMeasureType(l.measure_type)}</td>
            <td class="rate">${l.rate_value}</td>
            <td>${l.applies_because}</td>
        </tr>`;
    });

    if (stack.total_ad_valorem_estimate) {
        html += `<tr class="total">
            <td>TOTAL ESTIMATED</td>
            <td class="rate">${stack.total_ad_valorem_estimate}</td>
            <td></td>
        </tr>`;
    }

    html += '</tbody></table>';

    if (stack.conditional_basis?.length) {
        html += '<h3 style="margin-top:16px">Conditional Basis</h3>';
        stack.conditional_basis.forEach(item => { html += `<div class="warning">${item}</div>`; });
    }

    if (stack.unresolved_measures?.length) {
        html += '<h3 style="margin-top:16px">Unresolved Measures</h3><ul>';
        stack.unresolved_measures.forEach(item => { html += `<li>${item}</li>`; });
        html += '</ul>';
    }

    if (stack.source_versions?.length) {
        html += `<div class="note"><strong>Source versions:</strong> ${stack.source_versions.join(', ')}</div>`;
    }

    (stack.notes || []).forEach(n => { html += `<div class="note">${n}</div>`; });
    (stack.warnings || []).forEach(w => { html += `<div class="warning">${w}</div>`; });

    $('#duty-result').innerHTML = html;
}

function formatMeasureType(type) {
    const map = {
        'MFN': 'MFN Base Duty',
        'section_301': 'Section 301 (China)',
        'section_232': 'Section 232 (Aluminum)',
        'preferential': 'Preferential Rate',
        'gsp': 'EU GSP',
        'anti_dumping': 'Anti-Dumping Duty',
    };
    return map[type] || type;
}

// --- Audit Trail ---
function renderAudit(trail) {
    show('#audit-section');
    let html = '';

    if (trail.user_input) {
        html += `<div class="audit-step"><span class="step-name">Input:</span> ${trail.user_input}</div>`;
    }

    if (trail.effective_date) {
        html += `<div class="audit-step"><span class="step-name">Effective date:</span> ${trail.effective_date}</div>`;
    }

    if (trail.locked_digits?.length) {
        html += `<div class="audit-step"><span class="step-name">Locked digits:</span> ${trail.locked_digits.join(', ')}</div>`;
    }

    if (trail.assumptions?.length) {
        html += '<div class="audit-step"><span class="step-name">Assumptions:</span></div>';
        trail.assumptions.forEach(item => {
            html += `<div class="audit-step" style="margin-left:12px">${item}</div>`;
        });
    }

    if (trail.codes_considered?.length) {
        html += `<div class="audit-step"><span class="step-name">Codes considered:</span> ${trail.codes_considered.join(', ')}</div>`;
    }

    if (trail.codes_rejected?.length) {
        html += `<div class="audit-step"><span class="step-name">Codes rejected:</span> ${trail.codes_rejected.join(', ')}</div>`;
    }

    (trail.steps || []).forEach(s => {
        html += `<div class="audit-step"><span class="step-name">${s.step}:</span> ${s.detail}${s.source ? ` <em>[${s.source}]</em>` : ''}</div>`;
    });

    $('#audit-result').innerHTML = html || '<p>No audit trail available.</p>';
}
