/**
 * Mock Assessment Portal JavaScript Engine.
 * Handles timer countdown, anti-proctoring blur tracking, code running AJAX,
 * stepper navigation, and automated grading submissions.
 */

(function () {
  "use strict";

  const body = document.body;
  const sessionId = body.dataset.sessionId || "";
  const platform = body.dataset.platform || "";
  const strictProctoring = body.dataset.strictProctoring === "true";

  let proctorViolations = 0;
  let timerInterval = null;

  // --------------------------------------------------------------------------
  // 1. Anti-Proctoring Tab Blur Simulation
  // --------------------------------------------------------------------------
  const proctorBadge = document.getElementById("proctor-badge");
  const proctorModal = document.getElementById("proctor-modal");
  const dismissModalBtn = document.getElementById("btn-dismiss-proctor-warning");

  function triggerProctorEvent(eventType) {
    if (!sessionId) return;
    proctorViolations++;

    if (proctorBadge) {
      proctorBadge.dataset.violationCount = proctorViolations;
      proctorBadge.textContent = `Tab Switches: ${proctorViolations}`;
      proctorBadge.style.display = "inline-block";
    }

    if (proctorModal && strictProctoring) {
      proctorModal.style.display = "flex";
    }

    fetch("/portal/api/proctor-event", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        event_type: eventType,
        timestamp: new Date().toISOString()
      })
    }).catch((err) => {
      console.warn("Proctor event logging failed:", err);
    });
  }

  window.addEventListener("blur", () => triggerProctorEvent("blur"));
  document.addEventListener("visibilitychange", () => {
    if (document.hidden || document.visibilityState === "hidden") {
      triggerProctorEvent("visibilitychange");
    }
  });

  if (dismissModalBtn && proctorModal) {
    dismissModalBtn.addEventListener("click", () => {
      proctorModal.style.display = "none";
    });
  }

  // --------------------------------------------------------------------------
  // 2. Countdown Timer
  // --------------------------------------------------------------------------
  const timerContainer = document.getElementById("assessment-timer");
  const timerDisplay = document.getElementById("timer-display");

  if (timerContainer && timerDisplay) {
    let remaining = parseInt(timerContainer.dataset.secondsRemaining, 10);
    const total = parseInt(timerContainer.dataset.totalSeconds, 10) || remaining;

    function formatTime(sec) {
      if (sec < 0) sec = 0;
      const m = Math.floor(sec / 60);
      const s = sec % 60;
      return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
    }

    function tickTimer() {
      if (remaining <= 0) {
        clearInterval(timerInterval);
        timerDisplay.textContent = "00:00";
        timerContainer.dataset.secondsRemaining = "0";
        alert("Time limit reached! Submitting assessment automatically.");
        submitAssessment(true);
        return;
      }

      remaining--;
      timerContainer.dataset.secondsRemaining = remaining.toString();
      timerDisplay.textContent = formatTime(remaining);

      if (remaining <= 60) {
        timerContainer.classList.add("timer-urgent");
      }
    }

    timerDisplay.textContent = formatTime(remaining);
    timerInterval = setInterval(tickTimer, 1000);
  }

  // --------------------------------------------------------------------------
  // 3. Stepper Navigation
  // --------------------------------------------------------------------------
  const questions = Array.from(document.querySelectorAll(".question-container"));
  const prevBtn = document.getElementById("btn-prev-question");
  const nextBtn = document.getElementById("btn-next-question");
  const currentQIndexSpan = document.getElementById("current-q-index");
  const progressFill = document.getElementById("progress-fill");

  let currentIndex = 0;

  function updateStepper() {
    if (questions.length === 0) return;

    questions.forEach((q, idx) => {
      if (idx === currentIndex) {
        q.classList.add("active");
      } else {
        q.classList.remove("active");
      }
    });

    if (currentQIndexSpan) {
      currentQIndexSpan.textContent = (currentIndex + 1).toString();
    }

    if (progressFill) {
      const pct = Math.round(((currentIndex + 1) / questions.length) * 100);
      progressFill.style.width = `${pct}%`;
    }

    if (prevBtn) {
      prevBtn.disabled = currentIndex === 0;
    }

    if (nextBtn) {
      nextBtn.disabled = currentIndex === questions.length - 1;
    }
  }

  if (prevBtn) {
    prevBtn.addEventListener("click", () => {
      if (currentIndex > 0) {
        currentIndex--;
        updateStepper();
      }
    });
  }

  if (nextBtn) {
    nextBtn.addEventListener("click", () => {
      if (currentIndex < questions.length - 1) {
        currentIndex++;
        updateStepper();
      }
    });
  }

  // --------------------------------------------------------------------------
  // 4. Interactive Form Helpers (Sliders, Word Counters)
  // --------------------------------------------------------------------------
  document.querySelectorAll(".likert-slider").forEach((slider) => {
    const qid = slider.id.replace("slider-", "");
    const valBadge = document.getElementById(`slider-val-${qid}`);
    const labels = {
      1: "1: Strongly Disagree",
      2: "2: Disagree",
      3: "3: Neutral",
      4: "4: Agree",
      5: "5: Strongly Agree"
    };
    slider.addEventListener("input", (e) => {
      if (valBadge) {
        valBadge.textContent = labels[e.target.value] || e.target.value;
      }
    });
  });

  document.querySelectorAll(".essay-textarea").forEach((textarea) => {
    const qid = textarea.id.replace("textarea-", "");
    const wordCounter = document.getElementById(`word-count-${qid}`);
    textarea.addEventListener("input", (e) => {
      if (wordCounter) {
        const text = e.target.value.trim();
        const count = text ? text.split(/\s+/).length : 0;
        wordCounter.textContent = `${count} word${count === 1 ? "" : "s"}`;
      }
    });
  });

  // --------------------------------------------------------------------------
  // 5. Code Runner AJAX
  // --------------------------------------------------------------------------
  const runCodeBtn = document.getElementById("btn-run-code");
  const codeEditor = document.getElementById("code-editor");
  const consoleOutput = document.getElementById("console-output");
  const langSelect = document.getElementById("code-language-select");

  if (runCodeBtn && codeEditor && consoleOutput) {
    runCodeBtn.addEventListener("click", async () => {
      const qid = runCodeBtn.dataset.questionId || codeEditor.dataset.questionId;
      const code = codeEditor.value;
      const lang = langSelect ? langSelect.value : "python";

      runCodeBtn.disabled = true;
      consoleOutput.textContent = "Compiling and running test cases...\n";

      try {
        const resp = await fetch("/portal/api/run-code", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: sessionId,
            question_id: qid,
            language: lang,
            code: code
          })
        });

        const data = await resp.json();
        consoleOutput.textContent = `Status: ${data.status} (${data.test_cases_passed}/${data.total_test_cases} passed in ${data.runtime_ms}ms)\n\n${data.output}`;
      } catch (err) {
        consoleOutput.textContent = `Error executing code: ${err.message}`;
      } finally {
        runCodeBtn.disabled = false;
      }
    });
  }

  // --------------------------------------------------------------------------
  // 6. Assessment Submission & Answer Harvester
  // --------------------------------------------------------------------------
  const submitBtn = document.getElementById("btn-submit-assessment");

  function collectAnswers() {
    const answers = {};

    questions.forEach((q) => {
      const qid = q.dataset.questionId;
      const qtype = q.dataset.type;

      if (qtype === "single_choice") {
        const checked = q.querySelector('input[type="radio"]:checked');
        if (checked) {
          answers[qid] = {
            type: "single_choice",
            selected_option_id: checked.value
          };
        }
      } else if (qtype === "multiple_choice") {
        const checkedBoxes = Array.from(q.querySelectorAll('input[type="checkbox"]:checked'));
        answers[qid] = {
          type: "multiple_choice",
          selected_option_ids: checkedBoxes.map((cb) => cb.value)
        };
      } else if (qtype === "short_text") {
        const input = q.querySelector('input[type="text"]');
        if (input && input.value.trim()) {
          answers[qid] = {
            type: "short_text",
            text: input.value.trim()
          };
        }
      } else if (qtype === "essay") {
        const textarea = q.querySelector("textarea");
        if (textarea && textarea.value.trim()) {
          answers[qid] = {
            type: "essay",
            text: textarea.value.trim()
          };
        }
      } else if (qtype === "slider") {
        const slider = q.querySelector('input[type="range"]');
        if (slider) {
          answers[qid] = {
            type: "slider",
            value: parseInt(slider.value, 10)
          };
        }
      } else if (qtype === "ranking") {
        const selects = Array.from(q.querySelectorAll(".rank-select"));
        const rankingDict = {};
        selects.forEach((sel) => {
          const itemId = sel.dataset.itemId;
          if (sel.value) {
            rankingDict[itemId] = parseInt(sel.value, 10);
          }
        });
        answers[qid] = {
          type: "ranking",
          ranking: rankingDict
        };
      } else if (qtype === "code") {
        const textarea = q.querySelector(".code-textarea");
        if (textarea) {
          answers[qid] = {
            type: "code",
            code: textarea.value,
            language: langSelect ? langSelect.value : "python"
          };
        }
      }
    });

    return answers;
  }

  async function submitAssessment(isAuto = false) {
    if (timerInterval) clearInterval(timerInterval);

    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Grading Assessment...";
    }

    const answers = collectAnswers();
    const payload = {
      session_id: sessionId || "session-anonymous",
      platform: platform || "general",
      candidate_id: "cand_001",
      elapsed_time_seconds: 120.0,
      proctoring_violations: proctorViolations,
      answers: answers
    };

    try {
      const resp = await fetch("/portal/api/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (!resp.ok) {
        throw new Error(`Server returned HTTP ${resp.status}`);
      }

      const resData = await resp.json();
      if (resData.redirect_url) {
        window.location.href = resData.redirect_url;
      } else {
        window.location.href = `/portal/results/${payload.session_id}`;
      }
    } catch (err) {
      alert(`Submission error: ${err.message}`);
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = "Finish & Submit Assessment";
      }
    }
  }

  if (submitBtn) {
    submitBtn.addEventListener("click", () => submitAssessment(false));
  }

  // --------------------------------------------------------------------------
  // 7. Hub Controls (Reset State)
  // --------------------------------------------------------------------------
  const resetBtn = document.getElementById("btn-reset-state");
  const resetStatus = document.getElementById("reset-status");

  if (resetBtn) {
    resetBtn.addEventListener("click", async () => {
      resetBtn.disabled = true;
      try {
        const resp = await fetch("/portal/api/reset", { method: "POST" });
        if (resp.ok) {
          if (resetStatus) {
            resetStatus.style.display = "inline-block";
            setTimeout(() => {
              resetStatus.style.display = "none";
              window.location.reload();
            }, 1000);
          }
        }
      } catch (err) {
        alert(`Reset error: ${err.message}`);
      } finally {
        resetBtn.disabled = false;
      }
    });
  }

  // Initialize
  updateStepper();
})();
