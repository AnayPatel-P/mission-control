// Robot narrator: the monitor-head robot in the corner and its speech bubble.
const robot = document.getElementById("robot");
const bubble = document.getElementById("bubble");
const SQUINT_MS = 2000;
let squintTimer = 0;
let bubbleTimer = 0;
let talking = false;

export function setRobotState(state) {
  clearTimeout(squintTimer);
  robot.dataset.state = state;
  if (state === "squint") {
    squintTimer = setTimeout(() => { robot.dataset.state = talking ? "talking" : "idle"; }, SQUINT_MS);
  }
}

export function say(text) {
  if (!text) return;
  clearTimeout(bubbleTimer);
  bubble.textContent = text; // only the newest bubble shows
  bubble.classList.remove("off");
  bubble.classList.remove("pop");
  void bubble.offsetWidth; // restart the pop animation
  bubble.classList.add("pop");
  talking = true;
  if (robot.dataset.state !== "squint") setRobotState("talking");
  bubbleTimer = setTimeout(() => {
    bubble.classList.add("off"); // stays in the a11y tree so the next bubble is announced
    bubble.textContent = "";
    talking = false;
    if (robot.dataset.state === "talking") setRobotState("idle");
  }, Math.max(4000, text.length * 60));
}
