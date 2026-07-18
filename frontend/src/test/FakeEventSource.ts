type Listener = (event: MessageEvent<string>) => void;

export class FakeEventSource {
  static latest: FakeEventSource | undefined;

  readonly url: string;

  private readonly listeners = new Map<string, Listener[]>();

  constructor(url: string | URL) {
    this.url = String(url);
    FakeEventSource.latest = this;
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    const callback = listener as Listener;
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), callback]);
  }

  removeEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    const callback = listener as Listener;
    this.listeners.set(
      type,
      (this.listeners.get(type) ?? []).filter((item) => item !== callback),
    );
  }

  emit(type: string, payload: object, lastEventId: string) {
    const event = new MessageEvent("message", {
      data: JSON.stringify(payload),
      lastEventId,
    });
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }

  close() {}
}
