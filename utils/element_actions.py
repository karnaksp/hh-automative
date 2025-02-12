"""Elemnts utils"""


def set_value_with_event(element, value, driver):
    ACTION.move_to_element(element).click().perform()
    driver.execute_script("arguments[0].value = '';", element)
    driver.execute_script(
        """
        var setValue = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
        var element = arguments[0];
        var value = arguments[1];
        setValue.call(element, value);
        var event = new Event('input', { bubbles: true });
        element.dispatchEvent(event);
        """,
        element,
        value,
    )
