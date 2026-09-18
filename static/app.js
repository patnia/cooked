const CATEGORY_LABELS = {
  produce: "Produce",
  spices_pantry: "Spices & Pantry",
  dairy: "Dairy",
  grains: "Grains",
  other: "Other",
};

let currentRecipe = null; // { dish_name, serves, ingredients: [...] }
let lastResultData = null; // the last /api/generate response, if any

// --- step-by-step cooking mode ---
let cookSteps = []; // [{ text, timer_seconds }]
let currentStepIndex = 0;
let activeTimers = []; // [{ id, stepIndex, endsAt, durationSeconds, notified }]
let timerTickHandle = null;
let nextTimerId = 1;

const STORAGE_KEY = "wc_session";
const STORAGE_MAX_AGE_MS = 24 * 60 * 60 * 1000; // discard and start fresh after 24h

function $(id) {
  return document.getElementById(id);
}

function currentCheckedState() {
  const checkboxes = document.querySelectorAll("#ingredient-groups input[type=checkbox]");
  const state = {};
  checkboxes.forEach((cb) => {
    state[cb.dataset.index] = cb.checked;
  });
  return state;
}

function saveSession() {
  try {
    const activeView = document.querySelector(".view.active");
    const session = {
      view: activeView ? activeView.id : "view-input",
      dishInput: $("dish-input").value,
      preferences: collectPreferences(),
      currentRecipe,
      checkedState: currentCheckedState(),
      resultData: lastResultData,
      cookState: { currentStepIndex, activeTimers },
      savedAt: Date.now(),
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch (err) {
    // storage blocked (private browsing, quota, etc.) -- app still works without persistence
  }
}

function loadSession() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const session = JSON.parse(raw);
    if (!session.savedAt || Date.now() - session.savedAt > STORAGE_MAX_AGE_MS) {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return session;
  } catch (err) {
    return null;
  }
}

function clearSession() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch (err) {
    // ignore
  }
}

function restoreSession(session) {
  if (session.dishInput) $("dish-input").value = session.dishInput;

  const prefs = session.preferences || {};
  if (prefs.steps_mode) $("pref-steps-mode").value = prefs.steps_mode;
  if (prefs.skill_level) $("pref-skill-level").value = prefs.skill_level;
  if (prefs.appliance) $("pref-appliance").value = prefs.appliance;
  if (prefs.language) $("pref-language").value = prefs.language;

  if ((session.view === "view-result" || session.view === "view-steps") && session.resultData) {
    lastResultData = session.resultData;
    currentRecipe = session.currentRecipe;
    renderShoppingList(session.resultData);

    const cookState = session.cookState || {};
    currentStepIndex = cookState.currentStepIndex || 0;
    activeTimers = cookState.activeTimers || [];
    renderSteps(session.resultData);
    if (activeTimers.length > 0) ensureTimerTicking();

    showView(session.view);
  } else if (session.view === "view-checkoff" && session.currentRecipe) {
    currentRecipe = session.currentRecipe;
    renderCheckoff(currentRecipe, session.checkedState);
    showView("view-checkoff");
  } else {
    // view-input, or a saved view that no longer has the data to restore it
    // (e.g. resultData missing) -- always land on view-input explicitly rather
    // than assuming whatever the DOM's default active view happens to be.
    showView("view-input");
  }
}

function showView(id) {
  document.querySelectorAll(".view").forEach((el) => el.classList.remove("active"));
  $(id).classList.add("active");
  window.scrollTo(0, 0);
}

function showError(message) {
  const banner = $("error-banner");
  banner.textContent = message;
  banner.classList.remove("hidden");
}

function clearError() {
  const banner = $("error-banner");
  banner.textContent = "";
  banner.classList.add("hidden");
}

function setLoading(isLoading) {
  $("loading").classList.toggle("hidden", !isLoading);
}

function collectPreferences() {
  return {
    steps_mode: $("pref-steps-mode").value,
    skill_level: $("pref-skill-level").value,
    appliance: $("pref-appliance").value,
    language: $("pref-language").value,
  };
}

async function apiPost(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    const message = detail.detail || `Request failed (${response.status})`;
    if (response.status === 402) {
      throw new Error(
        `${message} (dev: visit /dev/set-tier?tier=paid1 to unlock locally)`
      );
    }
    throw new Error(message);
  }
  return response.json();
}

function renderCheckoff(recipe, checkedState) {
  $("checkoff-dish-name").textContent = recipe.dish_name;
  $("checkoff-serves").textContent = `Serves ${recipe.serves}`;

  const groups = {};
  recipe.ingredients.forEach((ingredient, index) => {
    const category = ingredient.category || "other";
    if (!groups[category]) groups[category] = [];
    groups[category].push({ ...ingredient, index });
  });

  const container = $("ingredient-groups");
  container.innerHTML = "";

  Object.entries(groups).forEach(([category, items]) => {
    const groupEl = document.createElement("div");
    groupEl.className = "ingredient-group";

    const heading = document.createElement("h3");
    heading.textContent = CATEGORY_LABELS[category] || category;
    groupEl.appendChild(heading);

    items.forEach((item) => {
      const row = document.createElement("label");
      row.className = "ingredient-row";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      // default: need to buy, unless restoring a saved checked/unchecked state
      checkbox.checked = checkedState ? checkedState[item.index] !== false : true;
      checkbox.dataset.index = item.index;

      const textWrap = document.createElement("div");
      const nameEl = document.createElement("span");
      nameEl.className = "ingredient-name";
      nameEl.textContent = item.name;
      const qtyEl = document.createElement("div");
      qtyEl.className = "ingredient-qty";
      qtyEl.textContent = item.needed_display;
      const buyEl = document.createElement("div");
      buyEl.className = "ingredient-buy";
      buyEl.textContent = `Buy: ${item.buy_display}`;
      textWrap.appendChild(nameEl);
      textWrap.appendChild(qtyEl);
      textWrap.appendChild(buyEl);

      row.appendChild(checkbox);
      row.appendChild(textWrap);
      groupEl.appendChild(row);
    });

    container.appendChild(groupEl);
  });
}

function collectKeptIngredients() {
  const checkboxes = document.querySelectorAll("#ingredient-groups input[type=checkbox]");
  const keptIndexes = new Set(
    Array.from(checkboxes)
      .filter((cb) => cb.checked)
      .map((cb) => Number(cb.dataset.index))
  );
  return currentRecipe.ingredients.filter((_, index) => keptIndexes.has(index));
}

function renderShoppingList(data) {
  $("result-dish-name").textContent = data.dish_name;

  const listEl = $("shopping-list");
  listEl.innerHTML = "";

  if (data.items.length === 0) {
    const empty = document.createElement("p");
    empty.className = "muted empty-shopping-list";
    empty.textContent = "You've already got everything -- nothing to buy!";
    listEl.appendChild(empty);
    return;
  }

  data.items.forEach((item) => {
    const card = document.createElement("div");
    card.className = "shopping-item";
    card.innerHTML = `
      <span class="ingredient-name">${item.name}</span>
      <span class="ingredient-qty">${item.needed_display}</span>
      <span class="ingredient-buy">Buy: ${item.buy_display}</span>
      <div class="shopping-links">
        <a href="${item.blinkit_url}" target="_blank" rel="noopener">Blinkit</a>
        <a href="${item.zepto_url}" target="_blank" rel="noopener">Zepto</a>
        <a href="${item.amazon_url}" target="_blank" rel="noopener">Amazon</a>
      </div>
    `;
    listEl.appendChild(card);
  });
}

function formatMMSS(totalSeconds) {
  const s = Math.max(0, Math.round(totalSeconds));
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

function notifyTimerDone() {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const ctx = new AudioCtx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.2, ctx.currentTime);
    osc.start();
    osc.stop(ctx.currentTime + 0.5);
    osc.onended = () => ctx.close();
  } catch (err) {
    // Web Audio unavailable -- silent no-op
  }
  try {
    if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
  } catch (err) {
    // ignore
  }
}

function renderTimersStrip() {
  const strip = $("timers-strip");

  if (activeTimers.length === 0) {
    strip.classList.add("hidden");
    strip.innerHTML = "";
    return;
  }

  strip.classList.remove("hidden");
  strip.innerHTML = "";
  const now = Date.now();

  activeTimers.forEach((timer) => {
    const remaining = (timer.endsAt - now) / 1000;
    const done = remaining <= 0;
    if (done && !timer.notified) {
      timer.notified = true;
      notifyTimerDone();
      saveSession();
    }

    const chip = document.createElement(done ? "button" : "div");
    if (done) chip.type = "button";
    chip.className = "timer-chip" + (done ? " done" : "");
    chip.textContent = done
      ? `Step ${timer.stepIndex + 1} done -- tap to clear`
      : `Step ${timer.stepIndex + 1} -- ${formatMMSS(remaining)}`;
    if (done) {
      chip.addEventListener("click", () => {
        activeTimers = activeTimers.filter((t) => t.id !== timer.id);
        renderTimersStrip();
        renderStepTimerAction();
        saveSession();
      });
    }
    strip.appendChild(chip);
  });
}

function ensureTimerTicking() {
  if (timerTickHandle) return;
  timerTickHandle = setInterval(() => {
    if (activeTimers.length === 0) {
      clearInterval(timerTickHandle);
      timerTickHandle = null;
      return;
    }
    renderTimersStrip();
    renderStepTimerAction();
  }, 1000);
}

function stopTimerTicking() {
  if (timerTickHandle) {
    clearInterval(timerTickHandle);
    timerTickHandle = null;
  }
}

function startTimerForStep(stepIndex, seconds) {
  activeTimers.push({
    id: nextTimerId++,
    stepIndex,
    endsAt: Date.now() + seconds * 1000,
    durationSeconds: seconds,
    notified: false,
  });
  renderTimersStrip();
  renderStepTimerAction();
  ensureTimerTicking();
  saveSession();
}

function renderStepTimerAction() {
  const btn = $("btn-start-timer");
  const step = cookSteps[currentStepIndex];
  if (!step || !step.timer_seconds) {
    btn.classList.add("hidden");
    return;
  }

  const existing = activeTimers.find((t) => t.stepIndex === currentStepIndex);
  btn.classList.remove("hidden");

  if (existing) {
    const remaining = (existing.endsAt - Date.now()) / 1000;
    btn.classList.add("running");
    btn.disabled = true;
    btn.textContent = remaining > 0 ? `Timer running -- ${formatMMSS(remaining)}` : "Timer done!";
  } else {
    btn.classList.remove("running");
    btn.disabled = false;
    btn.textContent = `Start timer (${formatMMSS(step.timer_seconds)})`;
    btn.onclick = () => startTimerForStep(currentStepIndex, step.timer_seconds);
  }
}

function renderCurrentStep() {
  const cookBlock = $("cook-block");
  if (cookSteps.length === 0) {
    cookBlock.classList.add("hidden");
    return;
  }
  cookBlock.classList.remove("hidden");

  $("step-counter").textContent = `Step ${currentStepIndex + 1} of ${cookSteps.length}`;
  $("step-text").textContent = cookSteps[currentStepIndex].text;
  renderStepTimerAction();

  $("btn-prev-step").disabled = currentStepIndex === 0;
  const isLast = currentStepIndex === cookSteps.length - 1;
  $("btn-next-step").textContent = isLast ? "Done cooking!" : "Next step →";
}

function goToStep(index) {
  currentStepIndex = Math.max(0, Math.min(cookSteps.length - 1, index));
  renderCurrentStep();
  saveSession();
}

function handleNextStep() {
  if (currentStepIndex === cookSteps.length - 1) {
    finishCooking();
  } else {
    goToStep(currentStepIndex + 1);
  }
}

function finishCooking() {
  stopTimerTicking();
  currentRecipe = null;
  lastResultData = null;
  cookSteps = [];
  currentStepIndex = 0;
  activeTimers = [];
  $("dish-input").value = "";
  showView("view-input");
  clearSession();
}

// data-driven: (re)loads which steps exist for the current recipe. Does NOT
// touch currentStepIndex/activeTimers -- callers set those explicitly first
// (reset to fresh for a new recipe, or restore from a saved session).
function renderSteps(data) {
  $("steps-dish-name").textContent = data.dish_name;

  const videoBlock = $("video-block");
  if (data.youtube_url) {
    $("youtube-link").href = data.youtube_url;
    videoBlock.classList.remove("hidden");
  } else {
    videoBlock.classList.add("hidden");
  }

  cookSteps = data.steps || [];
  if (currentStepIndex >= cookSteps.length) currentStepIndex = 0;
  renderCurrentStep();
  renderTimersStrip();
  if (activeTimers.length > 0) ensureTimerTicking();
}

// --- accounts: login / signup / onboarding ---

const ONBOARDING_QUESTIONS = {
  name: {
    type: "input",
    inputType: "text",
    prompt: "What should we call you?",
  },
  age: {
    type: "input",
    inputType: "number",
    prompt: "How old are you?",
  },
  content_preference: {
    type: "choice",
    prompt: "Do you want food, drinks, or both?",
    options: [
      { value: "food", label: "Food" },
      { value: "drink", label: "Drinks" },
      { value: "both", label: "Both" },
    ],
  },
  preferred_cuisine: {
    type: "choice",
    prompt: "Any preferred cuisine?",
    options: [
      { value: "indian", label: "Indian" },
      { value: "western", label: "Western" },
      { value: "none", label: "No preference" },
    ],
  },
  dietary_restriction: {
    type: "choice",
    prompt: "Any dietary restrictions?",
    options: [
      { value: "vegetarian", label: "Vegetarian" },
      { value: "vegan", label: "Vegan" },
      { value: "jain", label: "Jain" },
      { value: "non_vegetarian", label: "Non-vegetarian" },
      { value: "none", label: "No restrictions" },
    ],
  },
  drink_preference: {
    type: "choice",
    prompt: "Cocktails, mocktails, or both?",
    options: [
      { value: "cocktail", label: "Cocktails" },
      { value: "mocktail", label: "Mocktails" },
      { value: "both", label: "Both" },
    ],
  },
  skill_level: {
    type: "choice",
    prompt: "How comfortable are you in the kitchen?",
    options: [
      { value: "never_made_it", label: "Still learning the basics" },
      { value: "done_it_before", label: "I can follow a recipe" },
      { value: "comfortable", label: "I'm comfortable cooking" },
    ],
  },
  appliance: {
    type: "multi",
    prompt: "What do you cook with? (choose all that apply)",
    options: [
      { value: "stovetop", label: "Stovetop" },
      { value: "pressure_cooker", label: "Pressure cooker" },
      { value: "oven", label: "Oven" },
      { value: "air_fryer", label: "Air fryer" },
    ],
  },
  language: {
    type: "choice",
    prompt: "Preferred language for steps?",
    options: [
      { value: "english", label: "English" },
      { value: "hindi", label: "Hindi" },
    ],
  },
};

const MIN_DRINKING_AGE = 18;

function buildOnboardingQueue(contentPreference, age) {
  const common = ["skill_level", "appliance", "language"];
  const wantsDrinks = contentPreference === "drink" || contentPreference === "both";
  // under the drinking age: there's no real choice to offer, so skip the
  // question entirely rather than showing cocktails as a pickable option
  const drinkStep = wantsDrinks && age >= MIN_DRINKING_AGE ? ["drink_preference"] : [];

  if (contentPreference === "food") return ["preferred_cuisine", "dietary_restriction", ...common];
  if (contentPreference === "drink") return [...drinkStep, ...common];
  return [...drinkStep, "preferred_cuisine", "dietary_restriction", ...common]; // both: drinks first
}

let pendingSignup = null; // { email, password }
let onboardingAnswers = {};
let onboardingQueue = [];
let onboardingIndex = 0;
let onboardingMultiSelection = new Set();

function renderOnboardingQuestion(key) {
  const question = ONBOARDING_QUESTIONS[key];
  $("onboarding-prompt").textContent = question.prompt;

  const container = $("onboarding-options");
  container.innerHTML = "";
  const continueBtn = $("onboarding-continue");

  if (question.type === "choice") {
    continueBtn.classList.add("hidden");
    question.options.forEach((opt) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "choice-btn";
      btn.textContent = opt.label;
      btn.addEventListener("click", () => answerOnboarding(key, opt.value));
      container.appendChild(btn);
    });
    return;
  }

  if (question.type === "multi") {
    onboardingMultiSelection = new Set();
    question.options.forEach((opt) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "choice-btn";
      btn.textContent = opt.label;
      btn.addEventListener("click", () => {
        if (onboardingMultiSelection.has(opt.value)) {
          onboardingMultiSelection.delete(opt.value);
          btn.classList.remove("selected");
        } else {
          onboardingMultiSelection.add(opt.value);
          btn.classList.add("selected");
        }
      });
      container.appendChild(btn);
    });
    continueBtn.classList.remove("hidden");
    continueBtn.onclick = () => {
      if (onboardingMultiSelection.size === 0) {
        showError("Pick at least one.");
        return;
      }
      clearError();
      answerOnboarding(key, Array.from(onboardingMultiSelection));
    };
    return;
  }

  if (question.type === "input") {
    const input = document.createElement("input");
    input.type = question.inputType;
    input.id = "onboarding-input-field";
    if (question.inputType === "number") {
      input.min = "1";
      input.max = "120";
    }
    container.appendChild(input);
    continueBtn.classList.remove("hidden");
    continueBtn.onclick = () => {
      const raw = input.value.trim();
      if (question.inputType === "number") {
        const age = Number(raw);
        if (!raw || !Number.isInteger(age) || age < 1 || age > 120) {
          showError("Enter a valid age.");
          return;
        }
        clearError();
        answerOnboarding(key, age);
      } else {
        if (!raw) {
          showError("This one can't be blank.");
          return;
        }
        clearError();
        answerOnboarding(key, raw);
      }
    };
  }
}

function startOnboarding() {
  onboardingAnswers = {};
  onboardingQueue = ["name", "age", "content_preference"];
  onboardingIndex = 0;
  showView("view-onboarding");
  renderOnboardingQuestion(onboardingQueue[0]);
}

function answerOnboarding(key, value) {
  onboardingAnswers[key] = value;

  if (key === "content_preference") {
    const rest = buildOnboardingQueue(value, onboardingAnswers.age);
    onboardingQueue = ["name", "age", "content_preference", ...rest];
  }

  onboardingIndex += 1;

  if (onboardingIndex < onboardingQueue.length) {
    renderOnboardingQuestion(onboardingQueue[onboardingIndex]);
  } else {
    finishOnboarding();
  }
}

async function finishOnboarding() {
  clearError();
  setLoading(true);
  try {
    const wantsDrinks = onboardingAnswers.content_preference !== "food";
    const preferences = {
      name: onboardingAnswers.name,
      age: onboardingAnswers.age,
      content_preference: onboardingAnswers.content_preference,
      preferred_cuisine: onboardingAnswers.preferred_cuisine || "none",
      dietary_restriction: onboardingAnswers.dietary_restriction || "none",
      drink_preference:
        onboardingAnswers.drink_preference ||
        (wantsDrinks && onboardingAnswers.age < MIN_DRINKING_AGE ? "mocktail" : "none"),
      skill_level: onboardingAnswers.skill_level,
      appliance: onboardingAnswers.appliance,
      language: onboardingAnswers.language,
    };
    const user = await apiPost("/api/signup", {
      email: pendingSignup.email,
      password: pendingSignup.password,
      preferences,
    });
    pendingSignup = null;
    onLoggedIn(user);
    showView("view-input");
  } catch (err) {
    showError(err.message);
    showView("view-auth");
  } finally {
    setLoading(false);
  }
}

function applyAccountPreferences(preferences) {
  if (preferences.skill_level) $("pref-skill-level").value = preferences.skill_level;
  if (preferences.appliance && preferences.appliance.length) {
    $("pref-appliance").value = preferences.appliance[0];
  }
  if (preferences.language) $("pref-language").value = preferences.language;
}

function onLoggedIn(user) {
  $("account-email").textContent = user.email;
  $("account-bar").classList.remove("hidden");
  applyAccountPreferences(user.preferences);
}

function onLoggedOut() {
  $("account-bar").classList.add("hidden");
  $("account-email").textContent = "";
}

async function handleLogin() {
  clearError();
  const email = $("login-email").value.trim();
  const password = $("login-password").value;
  if (!email || !password) {
    showError("Enter your email and password.");
    return;
  }
  setLoading(true);
  try {
    const user = await apiPost("/api/login", { email, password });
    onLoggedIn(user);
    const savedSession = loadSession();
    if (savedSession) {
      restoreSession(savedSession);
    } else {
      showView("view-input");
    }
  } catch (err) {
    showError(err.message);
  } finally {
    setLoading(false);
  }
}

function handleSignupContinue() {
  clearError();
  const email = $("signup-email").value.trim();
  const password = $("signup-password").value;
  if (!email || !email.includes("@")) {
    showError("Enter a valid email address.");
    return;
  }
  if (password.length < 8) {
    showError("Password must be at least 8 characters.");
    return;
  }
  pendingSignup = { email, password };
  startOnboarding();
}

async function handleLogout() {
  clearError();
  try {
    await fetch("/api/logout", { method: "POST" });
  } catch (err) {
    // ignore -- clear client-side state regardless
  }
  onLoggedOut();
  clearSession();
  $("login-email").value = "";
  $("login-password").value = "";
  showView("view-landing");
}

function showAuth(mode) {
  clearError();
  $("auth-login").classList.toggle("hidden", mode !== "login");
  $("auth-signup").classList.toggle("hidden", mode !== "signup");
  showView("view-auth");
}

$("btn-landing-signup").addEventListener("click", () => showAuth("signup"));
$("btn-landing-login").addEventListener("click", () => showAuth("login"));
$("link-to-signup").addEventListener("click", (e) => {
  e.preventDefault();
  showAuth("signup");
});
$("link-to-login").addEventListener("click", (e) => {
  e.preventDefault();
  showAuth("login");
});
$("btn-login").addEventListener("click", handleLogin);
$("btn-signup-continue").addEventListener("click", handleSignupContinue);
$("btn-logout").addEventListener("click", (e) => {
  e.preventDefault();
  handleLogout();
});

document.querySelectorAll(".random-strip .chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    $("dish-input").value = chip.textContent.trim();
    handleExtract();
  });
});

// --- "not sure what to eat" quiz ---

const QUIZ_QUESTIONS = {
  category: {
    type: "choice",
    prompt: "Food, drink, or surprise me completely?",
    options: [
      { label: "Food", value: "food" },
      { label: "Drink", value: "drink" },
      { label: "Surprise me completely", value: "random" },
    ],
  },
  drink_type: {
    type: "choice",
    prompt: "Cocktail, mocktail, or either?",
    options: [
      { label: "Cocktail", value: "cocktail" },
      { label: "Mocktail", value: "mocktail" },
      { label: "Either", value: "either" },
    ],
  },
  cuisine: {
    type: "choice",
    prompt: "Indian, Western, or no preference?",
    options: [
      { label: "Indian", value: "indian" },
      { label: "Western", value: "western" },
      { label: "No preference", value: "none" },
    ],
  },
  diet: {
    type: "choice",
    prompt: "Any dietary restriction?",
    options: [
      { label: "Vegetarian", value: "vegetarian" },
      { label: "Vegan", value: "vegan" },
      { label: "Jain", value: "jain" },
      { label: "Non-vegetarian", value: "non_vegetarian" },
      { label: "No restriction", value: "none" },
    ],
  },
  mood: {
    type: "choice",
    prompt: "What are you in the mood for?",
    options: [
      { label: "Something quick & easy", value: "quick" },
      { label: "Hearty curry or rice", value: "curry_rice" },
      { label: "Pasta or noodles", value: "pasta" },
      { label: "Fresh bread", value: "bread" },
      { label: "Soup", value: "soup" },
      { label: "Something sweet", value: "sweet" },
      { label: "Surprise me", value: "surprise" },
    ],
  },
  protein: {
    type: "choice",
    prompt: "Any protein preference?",
    options: [
      { label: "Chicken", value: "chicken" },
      { label: "Seafood", value: "seafood" },
      { label: "Pork", value: "pork" },
      { label: "Beef", value: "beef" },
      { label: "Lamb", value: "lamb" },
      { label: "No preference", value: "none" },
    ],
  },
  servings: {
    type: "input",
    inputType: "number",
    prompt: "How many people are you cooking for?",
  },
};

// "how much time do you have" folds into the mood question itself (there's no
// stored per-recipe duration to filter on -- steps are generated live, never
// cached -- so a separate time question couldn't actually filter anything).
const QUIZ_MOOD_TAG_BUCKETS = {
  quick: ["snack", "salad", "fritter"],
  curry_rice: ["curry", "rice"],
  pasta: ["pasta"],
  bread: ["bread"],
  soup: ["soup"],
  sweet: ["dessert", "cake"],
  surprise: [],
};

let quizAnswers = {};

function renderQuizStep(step) {
  const q = QUIZ_QUESTIONS[step];
  $("quiz-prompt").textContent = q.prompt;
  const container = $("quiz-options");
  container.innerHTML = "";
  const continueBtn = $("quiz-continue");

  if (q.type === "input") {
    continueBtn.classList.remove("hidden");
    const input = document.createElement("input");
    input.type = q.inputType;
    input.id = "quiz-input-field";
    if (q.inputType === "number") {
      input.min = "1";
      input.max = "50";
      input.placeholder = "e.g. 4";
    }
    container.appendChild(input);
    continueBtn.onclick = () => {
      const raw = input.value.trim();
      const value = Number(raw);
      if (!raw || !Number.isInteger(value) || value < 1 || value > 50) {
        showError("Enter a valid number of people.");
        return;
      }
      clearError();
      quizAnswer(step, value);
    };
    return;
  }

  continueBtn.classList.add("hidden");
  q.options.forEach((opt) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "choice-btn";
    btn.textContent = opt.label;
    btn.addEventListener("click", () => quizAnswer(step, opt.value));
    container.appendChild(btn);
  });
}

function startQuiz() {
  clearError();
  quizAnswers = {};
  showView("view-quiz");
  renderQuizStep("category");
}

function quizAnswer(step, value) {
  quizAnswers[step] = value;

  if (step === "category") {
    if (value === "random") return renderQuizStep("servings");
    if (value === "drink") return renderQuizStep("drink_type");
    return renderQuizStep("cuisine");
  }
  if (step === "drink_type") return renderQuizStep("servings");
  if (step === "cuisine") return renderQuizStep("diet");
  if (step === "diet") return renderQuizStep("mood");
  if (step === "mood") {
    const proteinRelevant = quizAnswers.diet === "non_vegetarian" || quizAnswers.diet === "none";
    return proteinRelevant ? renderQuizStep("protein") : renderQuizStep("servings");
  }
  if (step === "protein") return renderQuizStep("servings");
  if (step === "servings") return finishQuiz();
}

async function finishQuiz() {
  clearError();
  setLoading(true);
  try {
    const category = quizAnswers.category;
    const tags = [];

    if (category === "drink") {
      if (quizAnswers.drink_type && quizAnswers.drink_type !== "either") {
        tags.push(quizAnswers.drink_type);
      }
    } else if (category === "food") {
      const bucket = QUIZ_MOOD_TAG_BUCKETS[quizAnswers.mood] || [];
      if (bucket.length) tags.push(bucket[Math.floor(Math.random() * bucket.length)]);
      if (quizAnswers.protein && quizAnswers.protein !== "none") tags.push(quizAnswers.protein);
    }

    const recipe = await apiPost("/api/quiz", {
      category,
      cuisine: quizAnswers.cuisine || "none",
      diet: quizAnswers.diet || "none",
      tags,
      serves: quizAnswers.servings,
    });
    currentRecipe = recipe;
    lastResultData = null;
    renderCheckoff(recipe);
    showView("view-checkoff");
    saveSession();
  } catch (err) {
    showError(err.message);
    showView("view-input");
  } finally {
    setLoading(false);
  }
}

$("link-take-quiz").addEventListener("click", (e) => {
  e.preventDefault();
  startQuiz();
});
$("btn-quiz-cancel").addEventListener("click", () => {
  clearError();
  showView("view-input");
});

async function handleExtract() {
  clearError();
  const text = $("dish-input").value.trim();
  if (!text) {
    showError("Tell us what you want to cook first.");
    return;
  }
  setLoading(true);
  try {
    const recipe = await apiPost("/api/extract", { text });
    currentRecipe = recipe;
    lastResultData = null;
    renderCheckoff(recipe);
    showView("view-checkoff");
    saveSession();
  } catch (err) {
    showError(err.message);
  } finally {
    setLoading(false);
  }
}

async function handleGenerate() {
  clearError();
  const kept = collectKeptIngredients();
  setLoading(true);
  try {
    const data = await apiPost("/api/generate", {
      dish_name: currentRecipe.dish_name,
      serves: currentRecipe.serves,
      ingredients: currentRecipe.ingredients,
      to_buy: kept,
      preferences: collectPreferences(),
    });
    lastResultData = data;
    stopTimerTicking();
    currentStepIndex = 0;
    activeTimers = [];
    renderShoppingList(data);
    renderSteps(data);
    showView("view-result");
    saveSession();
  } catch (err) {
    showError(err.message);
  } finally {
    setLoading(false);
  }
}

$("btn-extract").addEventListener("click", handleExtract);
$("btn-generate").addEventListener("click", handleGenerate);
$("btn-back-to-input").addEventListener("click", () => {
  clearError();
  showView("view-input");
  saveSession();
});
$("btn-ingredients-ready").addEventListener("click", () => {
  clearError();
  showView("view-steps");
  saveSession();
});
$("btn-back-to-shopping").addEventListener("click", () => {
  clearError();
  showView("view-result");
  saveSession();
});
$("btn-start-over").addEventListener("click", () => {
  clearError();
  finishCooking();
});
$("btn-prev-step").addEventListener("click", () => goToStep(currentStepIndex - 1));
$("btn-next-step").addEventListener("click", handleNextStep);

// Recompute timers immediately when the tab regains focus -- setInterval is
// throttled/paused while backgrounded, so this is what keeps a countdown
// accurate after the phone was locked or the tab was switched away.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && activeTimers.length > 0) {
    renderTimersStrip();
    renderStepTimerAction();
  }
});

// Persist on every meaningful change so backgrounding/reloading mid-flow doesn't lose progress.
$("ingredient-groups").addEventListener("change", (e) => {
  if (e.target.matches("input[type=checkbox]")) saveSession();
});
["pref-steps-mode", "pref-skill-level", "pref-appliance", "pref-language"].forEach((id) => {
  $(id).addEventListener("change", saveSession);
});
let dishInputSaveTimer = null;
$("dish-input").addEventListener("input", () => {
  clearTimeout(dishInputSaveTimer);
  dishInputSaveTimer = setTimeout(saveSession, 500);
});

async function init() {
  let user = null;
  try {
    const res = await fetch("/api/me");
    if (res.ok) user = await res.json();
  } catch (err) {
    // treat network hiccups as logged-out -- the auth view still lets them retry
  }

  if (user) {
    onLoggedIn(user);
    const savedSession = loadSession();
    if (savedSession) restoreSession(savedSession);
    else showView("view-input");
  } else {
    showView("view-landing");
  }

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        // non-fatal: app still works without the service worker
      });
    });
  }
}

init();
