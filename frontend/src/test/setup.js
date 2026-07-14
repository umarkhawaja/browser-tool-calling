import "@testing-library/jest-dom";

// jsdom doesn't implement scrollIntoView; stub it so components that auto-scroll
// don't throw during tests.
window.HTMLElement.prototype.scrollIntoView = () => {};
