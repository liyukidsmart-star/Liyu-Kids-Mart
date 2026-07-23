from datetime import datetime

def test():
    a = datetime.now()
    print("hello", a)
    from datetime import datetime as _dt
    print("ok")

test()
