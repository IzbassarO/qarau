import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

// jsdom has no top-layer/focus implementation; the browser uses native modal dialogs.
HTMLDialogElement.prototype.showModal = function () {
  this.setAttribute('open', '');
};
HTMLDialogElement.prototype.close = function () {
  this.removeAttribute('open');
};
afterEach(cleanup);
